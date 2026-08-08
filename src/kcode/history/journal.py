from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

from filelock import FileLock, Timeout

from kcode.conversation import ConversationMessage
from kcode.history.codec import encode_record, message_record
from kcode.history.ids import session_path, sessions_root_path
from kcode.history.models import (
    PersistenceState,
    SessionEndRecord,
    SessionMetadata,
    SessionRecord,
)


class SessionBusyError(RuntimeError):
    pass


class SessionJournalError(RuntimeError):
    pass


class SessionLease:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.path = session_dir / ".session.lock"
        self._lock = FileLock(str(self.path), thread_local=False)
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def acquire(self) -> None:
        if self._held:
            return
        if not self.session_dir.is_dir():
            raise SessionJournalError(f"Session directory does not exist: {self.session_dir}")
        if self.path.is_symlink() or self.path.resolve().parent != self.session_dir.resolve():
            raise SessionJournalError("Session lock path escapes its directory.")
        try:
            self._lock.acquire(timeout=0)
        except Timeout as exc:
            raise SessionBusyError(f"Session is already in use: {self.session_dir.name}") from exc
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        self._lock.release()
        self._held = False


class SessionJournal:
    def __init__(
        self,
        workspace_root: Path,
        metadata: SessionMetadata,
        *,
        sensitive_values: Sequence[str] = (),
        resume: bool = False,
        lease: SessionLease | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.sessions_root = sessions_root_path(self.workspace_root)
        self.session_dir = session_path(self.sessions_root, metadata.session_id)
        self.path = self.session_dir / "conversation.jsonl"
        self.metadata = metadata
        self.sensitive_values = tuple(
            sorted((value for value in sensitive_values if value), key=len, reverse=True)
        )
        self.resume = resume
        self.lease = lease or SessionLease(self.session_dir)
        self.state = PersistenceState.HEALTHY
        self.failure_reason: str | None = None
        self._opened = bool(resume and self.lease.held)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"kcode-journal-{metadata.session_id}",
        )

    def update_sensitive_values(self, values: Sequence[str]) -> None:
        self.sensitive_values = tuple(
            sorted((value for value in values if value), key=len, reverse=True)
        )

    async def append_checkpoint(self, messages: Sequence[ConversationMessage]) -> bool:
        if self.state == PersistenceState.CLOSED:
            raise SessionJournalError("Cannot append to a closed session journal.")
        if self.state == PersistenceState.DEGRADED:
            return False
        timestamp = time.time()
        records = tuple(
            record
            for message in messages
            if (record := message_record(message, timestamp)) is not None
        )
        if not records:
            return True
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._append_sync, records)
            return True
        except Exception as exc:
            self.state = PersistenceState.DEGRADED
            self.failure_reason = str(exc)
            return False

    async def close(self, reason: str = "exit") -> bool:
        if self.state == PersistenceState.CLOSED:
            return self.failure_reason is None
        success = self.state != PersistenceState.DEGRADED
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._close_sync, reason)
        except Exception as exc:
            self.failure_reason = self.failure_reason or str(exc)
            success = False
        finally:
            self.state = PersistenceState.CLOSED
            self._executor.shutdown(wait=True)
        return success

    def _append_sync(self, records: tuple[object, ...]) -> None:
        first = not self._opened
        self._ensure_open()
        lines: list[str] = []
        if first and not self.resume:
            lines.append(
                encode_record(
                    SessionRecord(
                        type="session",
                        schema=1,
                        session_id=self.metadata.session_id,
                        created_at=self.metadata.created_at,
                        provider=self.metadata.provider,
                        model=self.metadata.model,
                    )
                )
            )
        lines.extend(encode_record(record) for record in records)  # type: ignore[arg-type]
        self._write_lines(lines)

    def _ensure_open(self) -> None:
        if self._opened:
            return
        self.sessions_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._tighten_mode(self.sessions_root, 0o700)
        ignore = self.sessions_root / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
            self._tighten_mode(ignore, 0o600)
        self.session_dir.mkdir(mode=0o700, exist_ok=True)
        self._tighten_mode(self.session_dir, 0o700)
        self.lease.acquire()
        if self.path.is_symlink() or self.path.resolve().parent != self.session_dir:
            self.lease.release()
            raise SessionJournalError("conversation.jsonl escapes its session directory.")
        if self.resume:
            if not self.path.is_file():
                self.lease.release()
                raise SessionJournalError("Cannot resume a session without conversation.jsonl.")
        elif self.path.exists():
            self.lease.release()
            raise SessionJournalError("Refusing to overwrite an existing session journal.")
        self._opened = True

    def _write_lines(self, lines: Sequence[str]) -> None:
        mode = "a" if self.path.exists() else "x"
        with self.path.open(mode, encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(self._redact(line))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._tighten_mode(self.path, 0o600)

    def _close_sync(self, reason: str) -> None:
        try:
            if self._opened and self.failure_reason is None:
                end = SessionEndRecord(type="session_end", ts=time.time(), reason=reason)
                self._write_lines(
                    (encode_record(end),)
                )
        finally:
            self.lease.release()

    def _redact(self, line: str) -> str:
        def redact_value(value):
            if isinstance(value, str):
                for secret in self.sensitive_values:
                    value = value.replace(secret, "[REDACTED]")
                return value
            if isinstance(value, list):
                return [redact_value(item) for item in value]
            if isinstance(value, dict):
                return {key: redact_value(item) for key, item in value.items()}
            return value

        payload = redact_value(json.loads(line))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _tighten_mode(path: Path, mode: int) -> None:
        if os.name == "posix":
            path.chmod(mode)
