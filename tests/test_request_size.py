"""The 64 KB JSON cap must not apply to dataset Attach."""

from __future__ import annotations

from api.middleware import RequestSizeLimitMiddleware, is_upload_request
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

_JSON_LIMIT = 64 * 1024
_UPLOAD_LIMIT = 25 * 1024 * 1024


def _scope(*, method: str = "POST", path: str, content_type: str, raw_path: bytes | None = None) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode(),
        "headers": [(b"content-type", content_type.encode())],
    }


def test_a_multipart_post_is_an_upload() -> None:
    assert is_upload_request(
        _scope(
            path="/threads/abc/files",
            content_type="multipart/form-data; boundary=abc",
        )
    )


def test_a_json_run_is_not_an_upload() -> None:
    assert not is_upload_request(
        _scope(path="/threads/abc/runs", content_type="application/json")
    )


def test_upload_is_detected_from_raw_path_when_path_is_unhelpful() -> None:
    assert is_upload_request(
        _scope(
            path="/",
            raw_path=b"/threads/abc/files",
            content_type="application/octet-stream",
        )
    )


def _limited_app() -> RequestSizeLimitMiddleware:
    async def ok(_request):
        return JSONResponse({"ok": True})

    inner = Starlette(
        routes=[
            Route("/threads/{id}/files", ok, methods=["POST"]),
            Route("/threads/{id}/runs", ok, methods=["POST"]),
        ]
    )
    return RequestSizeLimitMiddleware(
        inner, max_bytes=_JSON_LIMIT, upload_max_bytes=_UPLOAD_LIMIT
    )


def test_a_500kb_attach_is_not_refused() -> None:
    csv = b"a,b\n" + b"1,2\n" * 80_000
    assert _JSON_LIMIT < len(csv) < _UPLOAD_LIMIT

    with TestClient(_limited_app()) as client:
        response = client.post(
            "/threads/00000000-0000-0000-0000-000000000001/files",
            files={"file": ("data.csv", csv, "text/csv")},
        )

    assert response.status_code == 200


def test_a_65kb_json_body_is_still_refused() -> None:
    with TestClient(_limited_app()) as client:
        response = client.post(
            "/threads/00000000-0000-0000-0000-000000000001/runs",
            content=b"x" * (_JSON_LIMIT + 1024),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "request too large"
