"""Per-user workspace: files for one account never land in another's directory."""

from __future__ import annotations

from pathlib import Path

import pytest
from mleng.core.workspace import (
    list_uploads,
    resolve_upload,
    safe_filename,
    save_upload,
    tracking_uri,
    user_root,
)


def test_safe_filename_rejects_paths() -> None:
    with pytest.raises(ValueError):
        safe_filename("../secret.csv")
    with pytest.raises(ValueError):
        safe_filename("nested/dir.csv")
    with pytest.raises(ValueError):
        safe_filename("notes.txt")
    assert safe_filename("churn.csv") == "churn.csv"


def test_two_users_do_not_share_files(tmp_path: Path) -> None:
    save_upload("user-a", "thread-1", "data.csv", b"a,b\n1,2\n", data_dir=tmp_path)
    save_upload("user-b", "thread-1", "data.csv", b"x,y\n9,8\n", data_dir=tmp_path)

    a = resolve_upload("user-a", "thread-1", "data.csv", data_dir=tmp_path)
    b = resolve_upload("user-b", "thread-1", "data.csv", data_dir=tmp_path)

    assert a.read_bytes() == b"a,b\n1,2\n"
    assert b.read_bytes() == b"x,y\n9,8\n"
    assert "user-a" in str(a)
    assert "user-b" in str(b)
    assert user_root("user-a", data_dir=tmp_path) != user_root("user-b", data_dir=tmp_path)


def test_tracking_uri_is_per_user(tmp_path: Path) -> None:
    uri_a = tracking_uri("user-a", data_dir=tmp_path)
    uri_b = tracking_uri("user-b", data_dir=tmp_path)
    assert uri_a.startswith("sqlite:")
    assert uri_b.startswith("sqlite:")
    assert str(tmp_path / "users" / "user-a") in uri_a
    assert str(tmp_path / "users" / "user-b") in uri_b


def test_latest_upload_is_used_when_name_omitted(tmp_path: Path) -> None:
    save_upload("u", "t", "first.csv", b"a\n1\n", data_dir=tmp_path)
    save_upload("u", "t", "second.csv", b"a\n2\n", data_dir=tmp_path)
    names = [item.name for item in list_uploads("u", "t", data_dir=tmp_path)]
    assert names[0] == "second.csv"
    assert resolve_upload("u", "t", data_dir=tmp_path).name == "second.csv"


def test_a_user_cannot_resolve_another_users_upload(tmp_path: Path) -> None:
    save_upload("user-a", "t", "data.csv", b"a\n1\n", data_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        resolve_upload("user-b", "t", "data.csv", data_dir=tmp_path)


@pytest.mark.parametrize("user_id", ["../escape", "/tmp", "a/b", "..", ".", ""])
def test_user_id_cannot_leave_the_users_folder(tmp_path: Path, user_id: str) -> None:
    with pytest.raises(ValueError):
        user_root(user_id, data_dir=tmp_path)
