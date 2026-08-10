from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
from pathlib import Path

import pytest

from kcode.conversation import AssistantMessage, UserMessage
from kcode.history.ids import create_session_id
from kcode.history.journal import (
    SessionBusyError,
    SessionJournal,
    SessionJournalError,
    SessionLease,
)
from kcode.history.models import PersistenceState, SessionMetadata


def metadata(session_id: str | None = None) -> SessionMetadata:
    return SessionMetadata(1, session_id or create_session_id(), time.time(), "fake", "model")


def try_lease_in_child(session_dir: str, queue) -> None:
    lease = SessionLease(Path(session_dir))
    try:
        lease.acquire()
    except SessionBusyError:
        queue.put("busy")
    else:
        queue.put("acquired")
        lease.release()


@pytest.mark.asyncio
async def test_journal_is_lazy_writes_header_checkpoint_and_end_with_permissions(tmp_path) -> None:
    journal = SessionJournal(tmp_path, metadata())
    assert not journal.session_dir.exists()

    assert await journal.append_checkpoint((UserMessage("hello"), AssistantMessage("hi")))
    assert journal.path.is_file()
    lines = journal.path.read_text(encoding="utf-8").splitlines()
    assert '"type":"session"' in lines[0]
    assert [line.count('"type":"message"') for line in lines[1:]] == [1, 1]
    assert (journal.sessions_root / ".gitignore").read_text(encoding="utf-8") == "*\n"
    if os.name == "posix":
        assert journal.session_dir.stat().st_mode & 0o777 == 0o700
        assert journal.path.stat().st_mode & 0o777 == 0o600

    assert await journal.close()
    assert '"type":"session_end"' in journal.path.read_text(encoding="utf-8").splitlines()[-1]
    assert journal.state == PersistenceState.CLOSED


@pytest.mark.asyncio
async def test_journal_lock_blocks_second_writer_and_releases_on_close(tmp_path) -> None:
    journal = SessionJournal(tmp_path, metadata())
    await journal.append_checkpoint((UserMessage("hello"), AssistantMessage("hi")))
    competing = SessionLease(journal.session_dir)
    with pytest.raises(SessionBusyError):
        competing.acquire()

    await journal.close()
    competing.acquire()
    assert competing.held
    competing.release()


@pytest.mark.asyncio
async def test_journal_redacts_known_values_and_degrades_after_fsync_failure(
    tmp_path, monkeypatch
) -> None:
    secret = 'key-"secret"'
    journal = SessionJournal(tmp_path, metadata(), sensitive_values=(secret,))
    assert await journal.append_checkpoint((UserMessage(secret), AssistantMessage("ok")))
    assert secret not in journal.path.read_text(encoding="utf-8")
    await journal.close()

    structural = SessionJournal(tmp_path, metadata(), sensitive_values=("type",))
    assert await structural.append_checkpoint((UserMessage("type"), AssistantMessage("ok")))
    assert '"type":"session"' in structural.path.read_text(encoding="utf-8")
    await structural.close()

    broken = SessionJournal(tmp_path, metadata())

    def fail_fsync(_fd: int) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    assert not await broken.append_checkpoint((UserMessage("x"), AssistantMessage("y")))
    assert broken.state == PersistenceState.DEGRADED
    assert "disk unavailable" in (broken.failure_reason or "")
    assert not await broken.append_checkpoint((UserMessage("later"),))
    assert not await broken.close()


@pytest.mark.asyncio
async def test_concurrent_checkpoint_submissions_remain_ordered(tmp_path) -> None:
    journal = SessionJournal(tmp_path, metadata())
    results = await __import__("asyncio").gather(
        journal.append_checkpoint((UserMessage("one"),)),
        journal.append_checkpoint((AssistantMessage("two"),)),
    )
    assert results == [True, True]
    await journal.close()
    text = journal.path.read_text(encoding="utf-8")
    assert text.index('"content":"one"') < text.index('"content":"two"')


@pytest.mark.asyncio
async def test_skill_state_is_appended_without_skill_body(tmp_path) -> None:
    journal = SessionJournal(tmp_path, metadata())
    assert await journal.append_skill_state(("review", "test"))
    text = journal.path.read_text(encoding="utf-8")
    assert '"type":"skill_state"' in text
    assert '"names":["review","test"]' in text
    await journal.close()


def test_lease_rejects_symlink_lock(tmp_path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_text("do not touch", encoding="utf-8")
    (session_dir / ".session.lock").symlink_to(outside)
    with pytest.raises(SessionJournalError, match="escapes"):
        SessionLease(session_dir).acquire()


@pytest.mark.asyncio
async def test_lease_blocks_another_process_and_recovers_after_release(tmp_path) -> None:
    journal = SessionJournal(tmp_path, metadata())
    await journal.append_checkpoint((UserMessage("held"), AssistantMessage("answer")))
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=try_lease_in_child, args=(str(journal.session_dir), queue))
    process.start()
    process.join(5)
    assert process.exitcode == 0
    assert queue.get(timeout=1) == "busy"

    await journal.close()
    process = context.Process(target=try_lease_in_child, args=(str(journal.session_dir), queue))
    process.start()
    process.join(5)
    assert process.exitcode == 0
    assert queue.get(timeout=1) == "acquired"


@pytest.mark.asyncio
async def test_slow_fsync_does_not_block_the_event_loop(tmp_path, monkeypatch) -> None:
    journal = SessionJournal(tmp_path, metadata())
    real_fsync = os.fsync

    def slow_fsync(fd: int) -> None:
        time.sleep(0.1)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", slow_fsync)
    task = asyncio.create_task(
        journal.append_checkpoint((UserMessage("slow"), AssistantMessage("still responsive")))
    )
    await asyncio.sleep(0.01)
    assert not task.done()
    assert await task
    await journal.close()
