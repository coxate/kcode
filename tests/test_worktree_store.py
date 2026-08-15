from __future__ import annotations

import json
from pathlib import Path

import pytest

from kcode.worktrees.models import WorktreeKind, WorktreeRecord, WorktreeStoreError
from kcode.worktrees.store import WorktreeStore


def make_store(tmp_path: Path) -> WorktreeStore:
    repo = tmp_path / "repo"
    repo.mkdir()
    return WorktreeStore(repo, tmp_path / ".kcode-worktrees" / "repo")


def test_store_round_trip_outside_repository(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = WorktreeRecord(
        "demo",
        store.worktree_root / "demo",
        "kcode-worktree/demo",
        "a" * 40,
        WorktreeKind.MANUAL,
        None,
        1.0,
    )
    store.save({"demo": record})
    assert store.load() == {"demo": record}
    assert store.path.parent.parent.parent == tmp_path
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("change", ("version", "repo_root", "path"))
def test_store_fails_closed_for_untrusted_metadata(tmp_path: Path, change: str) -> None:
    store = make_store(tmp_path)
    record = WorktreeRecord(
        "demo",
        store.worktree_root / "demo",
        "kcode-worktree/demo",
        "a" * 40,
        WorktreeKind.MANUAL,
        None,
        1.0,
    )
    store.save({"demo": record})
    payload = json.loads(store.path.read_text())
    if change == "version":
        payload["version"] = 2
    elif change == "repo_root":
        payload["repo_root"] = str(tmp_path / "other")
    else:
        payload["records"][0]["path"] = str(tmp_path / "escape")
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorktreeStoreError):
        store.load()


def test_store_rejects_symlink_root_and_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / ".kcode-worktrees" / "repo"
    linked_root.parent.mkdir()
    linked_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorktreeStoreError, match="符号链接"):
        WorktreeStore(repo, linked_root)

    safe_root = tmp_path / "safe" / "repo"
    store = WorktreeStore(repo, safe_root)
    safe_root.mkdir(parents=True)
    target = outside / "metadata.json"
    target.write_text("{}", encoding="utf-8")
    store.path.symlink_to(target)
    with pytest.raises(WorktreeStoreError, match="符号链接"):
        store.load()
    with pytest.raises(WorktreeStoreError, match="符号链接"):
        store.save({})
    assert target.read_text(encoding="utf-8") == "{}"
