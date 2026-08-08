import os
import time
from pathlib import Path

import pytest
from filelock import FileLock

from kcode.memory import (
    MemoryProposal,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    MemoryStore,
    MemoryStoreBusy,
    MemoryStoreError,
    MemoryType,
)
from kcode.memory.models import MemoryAction, proposal_id
from kcode.memory.paths import MemoryPathError


def record(scope: MemoryScope = MemoryScope.PROJECT) -> MemoryRecord:
    now = time.time()
    memory_type = (
        MemoryType.PROJECT_FACT
        if scope == MemoryScope.PROJECT
        else MemoryType.USER_PREFERENCE
    )
    return MemoryRecord(
        id="mem_" + "a" * 32,
        type=memory_type,
        scope=scope,
        title="Package manager",
        summary="The project uses uv.",
        application="Use uv for Python commands.",
        body="Confirmed by the user.",
        source_session_id="session-1",
        source_turn_hash="b" * 64,
        created_at=now,
        updated_at=now,
    )


def proposal(scope: MemoryScope = MemoryScope.PROJECT) -> MemoryProposal:
    values = {"scope": scope.value, "title": "Use uv", "source": "c" * 64}
    memory_type = (
        MemoryType.PROJECT_FACT
        if scope == MemoryScope.PROJECT
        else MemoryType.USER_PREFERENCE
    )
    return MemoryProposal(
        id=proposal_id(values),
        action=MemoryAction.CREATE,
        type=memory_type,
        scope=scope,
        title="Use uv",
        summary="The project uses uv.",
        application="Run Python tasks with uv.",
        reason="Durable project convention.",
        evidence="We use uv.",
        source_session_id="session-1",
        source_turn_hash="c" * 64,
        created_at=time.time(),
    )


def test_record_candidate_and_status_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(MemoryScope.PROJECT, tmp_path, home=tmp_path / "home")
    item = record()
    store.save(item)
    candidate = proposal()
    assert store.save_proposal(candidate)
    assert not store.save_proposal(candidate)

    snapshot = store.load()
    assert snapshot.records == (item,)
    assert snapshot.proposals == (candidate,)
    assert "The project uses uv" in store.paths.index.read_text(encoding="utf-8")

    updated = store.set_status(item.id, MemoryStatus.INACTIVE)
    assert updated.status == MemoryStatus.INACTIVE
    assert "The project uses uv" not in store.paths.index.read_text(encoding="utf-8")

    store.resolve_proposal(candidate.id)
    assert store.pending() == ()
    assert candidate.id in store.load_state().processed_proposal_hashes


def test_permissions_and_secret_redaction(tmp_path: Path) -> None:
    store = MemoryStore(
        MemoryScope.PROJECT,
        tmp_path,
        home=tmp_path / "home",
        sensitive_values=("super-secret",),
    )
    item = record().model_copy(update={"body": "value=super-secret"})
    store.save(item)
    raw = (store.paths.entries / f"{item.id}.md").read_text(encoding="utf-8")
    assert "super-secret" not in raw
    assert "[REDACTED]" in raw
    if os.name == "posix":
        assert store.paths.root.stat().st_mode & 0o777 == 0o700
        assert (store.paths.entries / f"{item.id}.md").stat().st_mode & 0o777 == 0o600


def test_bad_record_is_isolated_and_index_rebuilt(tmp_path: Path) -> None:
    store = MemoryStore(MemoryScope.PROJECT, tmp_path, home=tmp_path / "home")
    store.save(record())
    (store.paths.entries / "broken.md").write_text("not frontmatter", encoding="utf-8")
    store.paths.index.write_text("tampered", encoding="utf-8")
    snapshot = store.load()
    assert len(snapshot.records) == 1
    assert any("broken.md" in warning for warning in snapshot.warnings)
    assert "tampered" not in store.paths.index.read_text(encoding="utf-8")


def test_symlink_root_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    memory = tmp_path / ".kcode" / "memory"
    memory.parent.mkdir()
    memory.symlink_to(outside, target_is_directory=True)
    with pytest.raises((MemoryStoreError, MemoryPathError)):
        MemoryStore(MemoryScope.PROJECT, tmp_path)


def test_symlink_parent_cannot_move_memory_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".kcode").symlink_to(outside, target_is_directory=True)
    with pytest.raises(MemoryPathError, match="escapes its allowed boundary"):
        MemoryStore(MemoryScope.PROJECT, workspace)


def test_external_lock_prevents_concurrent_overwrite(tmp_path: Path) -> None:
    store = MemoryStore(MemoryScope.PROJECT, tmp_path)
    store.load()
    lock = FileLock(str(store.paths.lock))
    lock.acquire(timeout=0)
    try:
        with pytest.raises(MemoryStoreBusy, match="in use"):
            store.save(record())
    finally:
        lock.release()


def test_atomic_replace_failure_preserves_previous_record(tmp_path: Path, monkeypatch) -> None:
    store = MemoryStore(MemoryScope.PROJECT, tmp_path)
    original = record()
    store.save(original)

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("kcode.memory.store.os.replace", fail_replace)
    changed = original.model_copy(update={"title": "Changed", "updated_at": time.time() + 1})
    with pytest.raises(OSError, match="simulated"):
        store.save(changed)
    assert store.get(original.id).title == original.title
    assert not tuple(store.paths.entries.glob(".memory-*"))
