"""ASGI middleware that must not sit on BaseHTTPMiddleware.

Starlette's ``BaseHTTPMiddleware`` buffers the response, which would stall an
SSE stream for the length of a run. Everything here is a thin ASGI wrapper so a
byte that the inner app sends is a byte the client receives.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .settings import ApiSettings

logger = logging.getLogger("api.http")

_EXEMPT_PATHS = frozenset({"/health", "/ready"})
_JSON_413 = b'{"detail":"request too large"}'
_FILE_413 = b'{"detail":"file too large (max 25 MB)"}'
_JSON_429 = b'{"detail":"too many requests"}'
_UPLOAD_PATH = re.compile(r"/threads/[^/]+/files/?$")


def _path(scope: Scope) -> str:
    path = scope.get("path") or "/"
    if "//" in path:
        path = re.sub(r"/{2,}", "/", path)
    return path


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value.decode("latin-1")
    return ""


def _path_candidates(scope: Scope) -> list[str]:
    """Every string that might be the request path, depending on the server."""
    path = scope.get("path") or "/"
    raw = scope.get("raw_path") or b""
    raw_s = raw.decode("latin-1").split("?")[0] if isinstance(raw, (bytes, bytearray)) else str(raw)
    root = (scope.get("root_path") or "").rstrip("/")
    combined = f"{root}{path}" if root and not path.startswith(root) else path
    seen: list[str] = []
    for candidate in (path, raw_s, combined):
        text = candidate.replace("\\", "/")
        if "//" in text:
            text = re.sub(r"/{2,}", "/", text)
        if text and text not in seen:
            seen.append(text)
    return seen


def is_upload_request(scope: Scope) -> bool:
    """True for dataset Attach: multipart POST, or POST to /threads/{id}/files."""
    if (scope.get("method") or "").upper() != "POST":
        return False
    content_type = _header(scope, b"content-type").lower()
    if content_type.startswith("multipart/form-data"):
        return True
    for path in _path_candidates(scope):
        stripped = path.rstrip("/")
        if "/threads/" in stripped and stripped.endswith("/files"):
            return True
        if _UPLOAD_PATH.search(path):
            return True
    return False


def _client_host(scope: Scope) -> str:
    client = scope.get("client")
    if not client:
        return "unknown"
    return str(client[0])


async def _send_json(send: Send, status: int, body: bytes, retry_after: int | None = None) -> None:
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if retry_after is not None:
        headers.append((b"retry-after", str(retry_after).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class RequestContextMiddleware:
    """Assign a request id, echo it, and log method/path/status — never the query."""

    def __init__(self, app: ASGIApp, settings: ApiSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = ""
        for key, value in scope.get("headers") or []:
            if key == b"x-request-id":
                request_id = value.decode("latin-1").strip()
                break
        if not request_id:
            request_id = uuid.uuid4().hex
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        started = time.perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            if self.settings.environment == "test":
                return
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "%s %s %s",
                scope.get("method"),
                _path(scope),
                status_code,
                extra={
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": _path(scope),
                    "status": status_code,
                    "ms": elapsed_ms,
                    "user_id": scope.get("state", {}).get("user_id"),
                },
            )


class RequestSizeLimitMiddleware:
    """Refuse a body larger than the cap for this route.

    Trusts ``Content-Length`` when it is present (browsers and our UI always send
    it) and counts chunks when it is not, so omitting the header is not a way
    around the cap. Dataset uploads use a larger limit than JSON chat bodies.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        upload_max_bytes: int | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes or max_bytes

    def _limit_for(self, scope: Scope) -> int:
        if is_upload_request(scope):
            return self.upload_max_bytes
        return self.max_bytes

    def _too_large_body(self, scope: Scope) -> bytes:
        return _FILE_413 if is_upload_request(scope) else _JSON_413

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = self._limit_for(scope)
        refused = self._too_large_body(scope)
        declared = _content_length(scope)
        if declared is not None and declared > max_bytes:
            await _send_json(send, 413, refused)
            return

        received = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request" and not too_large:
                received += len(message.get("body") or b"")
                if received > max_bytes:
                    too_large = True
            return message

        if declared is not None:
            await self.app(scope, receive, send)
            return

        async def send_or_413(message: Message) -> None:
            if too_large and message["type"] == "http.response.start":
                await _send_json(send, 413, refused)
                return
            if too_large:
                return
            await send(message)

        await self.app(scope, limited_receive, send_or_413)


class RateLimitMiddleware:
    """Fixed window, per client IP, stored in Redis so replicas share the budget.

    ``/health`` and ``/ready`` are exempt: a probe that 429s looks like an
    instance that should be killed.
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: ApiSettings,
        redis_factory: Callable[[], object],
    ) -> None:
        self.app = app
        self.settings = settings
        self.redis_factory = redis_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.settings.rate_limit_requests <= 0 or _path(scope) in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        redis = self.redis_factory()
        key = f"rl:{_client_host(scope)}"
        count = int(redis.incr(key))
        if count == 1:
            redis.expire(key, self.settings.rate_limit_window_seconds)
        if count > self.settings.rate_limit_requests:
            retry_after = int(redis.ttl(key) or self.settings.rate_limit_window_seconds)
            await _send_json(send, 429, _JSON_429, retry_after=max(retry_after, 1))
            return

        await self.app(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers") or []:
        if key == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
