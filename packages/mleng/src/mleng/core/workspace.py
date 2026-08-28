"""Per-user on-disk layout for uploads and MLflow tracking.

Every user's files live under ``{data_dir}/users/{user_id}/``. Threads nest
inside that, so two accounts never share a directory even if they pick the same
filename. Tools resolve paths only through this module; a model-supplied name
is treated as a basename, never as a path.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import shutil

_ALLOWED_SUFFIXES = {".csv", ".parquet", ".pq"}
_MAX_NAME_CHARS = 200
# UUIDs, plus simple tokens used by the CLI (`cli`, `local`) and tests.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class RunContext:
    user_id: str
    thread_id: str
    data_dir: Path


@dataclass(frozen=True)
class StoredFile:
    name: str
    size: int
    modified_at: datetime
    path: Path


_current: ContextVar[RunContext | None] = ContextVar("mleng_run_context", default=None)


def default_data_dir() -> Path:
    env = os.getenv("MLENG_DATA_DIR")
    if env:
        return Path(env)
    for base in (Path.cwd(), Path(__file__).resolve()):
        for parent in (base, *base.parents):
            if (parent / "pyproject.toml").is_file() and (parent / "packages").is_dir():
                return parent / "data"
    return Path.cwd() / "data"


def _dir(data_dir: Path | None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    ctx = _current.get()
    if ctx is not None:
        return ctx.data_dir
    return default_data_dir()


def set_run_context(
    user_id: str,
    thread_id: str,
    *,
    data_dir: Path | None = None,
) -> Token:
    """Bind this task to one user's workspace. Reset the token when the run ends."""
    ctx = RunContext(
        user_id=str(user_id),
        thread_id=str(thread_id),
        data_dir=Path(data_dir) if data_dir is not None else default_data_dir(),
    )
    return _current.set(ctx)


def reset_run_context(token: Token) -> None:
    _current.reset(token)


def current() -> RunContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("no run context: set_run_context() before using workspace tools")
    return ctx


def _safe_id(value: str, *, kind: str) -> str:
    """Reject anything that could be used as a path segment to leave ``users/``."""
    text = str(value).strip()
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"invalid {kind}")
    return text


def user_root(user_id: str, *, data_dir: Path | None = None) -> Path:
    """``{data_dir}/users/{user_id}`` — the only root that user's files may occupy."""
    users = (_dir(data_dir) / "users").resolve()
    users.mkdir(parents=True, exist_ok=True)
    root = (users / _safe_id(user_id, kind="user id")).resolve()
    if not root.is_relative_to(users):
        raise ValueError("user workspace is outside the data directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


def uploads_dir(user_id: str, thread_id: str, *, data_dir: Path | None = None) -> Path:
    root = user_root(user_id, data_dir=data_dir)
    folder = (root / "uploads" / _safe_id(thread_id, kind="thread id")).resolve()
    if not folder.is_relative_to(root):
        raise ValueError("thread uploads are outside this user's workspace")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def artifacts_dir(user_id: str, *, data_dir: Path | None = None) -> Path:
    folder = user_root(user_id, data_dir=data_dir) / "artifacts"
    folder.mkdir(parents=True, exist_ok=True)
    return folder.resolve()


def tracking_uri(user_id: str, *, data_dir: Path | None = None) -> str:
    """SQLite tracking store for this user only. Never a shared MLflow server path."""
    db = user_root(user_id, data_dir=data_dir) / "mlflow.db"
    return "sqlite:///" + db.resolve().as_posix()


def artifact_uri(user_id: str, *, data_dir: Path | None = None) -> str:
    """Where this user's run artifacts are written."""
    return artifacts_dir(user_id, data_dir=data_dir).as_uri()


def safe_filename(name: str) -> str:
    """Reject anything that could walk out of the uploads folder."""
    raw = (name or "").strip()
    if not raw:
        raise ValueError("filename is empty")
    if "/" in raw.replace("\\", "/") or raw in {".", ".."}:
        raise ValueError("filename must not contain a path")
    base = Path(raw).name
    if base != raw or not base or len(base) > _MAX_NAME_CHARS:
        raise ValueError("invalid filename")
    if Path(base).suffix.lower() not in _ALLOWED_SUFFIXES:
        raise ValueError("only .csv and .parquet files are accepted")
    return base


def _contained(path: Path, folder: Path) -> Path:
    resolved = path.resolve()
    folder = folder.resolve()
    if not resolved.is_relative_to(folder):
        raise ValueError("filename is outside this user's workspace")
    return resolved


def save_upload(
    user_id: str,
    thread_id: str,
    filename: str,
    data: bytes,
    *,
    data_dir: Path | None = None,
) -> StoredFile:
    name = safe_filename(filename)
    folder = uploads_dir(user_id, thread_id, data_dir=data_dir)
    path = _contained(folder / name, folder)
    path.write_bytes(data)
    return _stored(path)


def list_uploads(
    user_id: str, thread_id: str, *, data_dir: Path | None = None
) -> list[StoredFile]:
    folder = uploads_dir(user_id, thread_id, data_dir=data_dir)
    files = [_stored(p) for p in folder.iterdir() if p.is_file()]
    files.sort(key=lambda item: item.modified_at, reverse=True)
    return files


def resolve_upload(
    user_id: str,
    thread_id: str,
    filename: str | None = None,
    *,
    data_dir: Path | None = None,
) -> Path:
    """The named file, or the most recently modified one on this thread."""
    folder = uploads_dir(user_id, thread_id, data_dir=data_dir)
    if filename:
        path = _contained(folder / safe_filename(filename), folder)
        if not path.is_file():
            raise FileNotFoundError(f"no file named {filename!r} on this conversation")
        return path
    files = list_uploads(user_id, thread_id, data_dir=data_dir)
    if not files:
        raise FileNotFoundError("no dataset uploaded on this conversation yet")
    return files[0].path


def delete_thread_uploads(
    user_id: str, thread_id: str, *, data_dir: Path | None = None
) -> None:
    root = user_root(user_id, data_dir=data_dir)
    folder = (root / "uploads" / _safe_id(thread_id, kind="thread id")).resolve()
    if not folder.is_relative_to((root / "uploads").resolve()):
        raise ValueError("thread uploads are outside this user's workspace")
    if folder.exists():
        shutil.rmtree(folder)


def _stored(path: Path) -> StoredFile:
    stat = path.stat()
    return StoredFile(
        name=path.name,
        size=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        path=path,
    )
