from __future__ import annotations

from itertools import islice
from pathlib import Path

from kcode.conversation import (
    AssistantMessage,
    Conversation,
    ConversationMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.history.codec import HistoryCodecError, decode_message, decode_record
from kcode.history.ids import session_path, sessions_root_path, validate_session_id
from kcode.history.journal import SessionBusyError, SessionLease
from kcode.history.models import (
    LoadedSession,
    MessageRecord,
    SessionEndRecord,
    SessionMetadata,
    SessionRecord,
    SessionSummary,
    SkillStateRecord,
)
from kcode.tools.base import ToolResult

LIST_READ_LIMIT = 1024 * 1024
SESSION_SCAN_LIMIT = 10_000
TITLE_LIMIT = 80


class SessionStoreError(RuntimeError):
    pass


class SessionStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.sessions_root = sessions_root_path(self.workspace_root)

    def list_sessions(self, *, exclude_session_id: str | None = None) -> tuple[SessionSummary, ...]:
        if not self.sessions_root.is_dir():
            return ()
        summaries: list[SessionSummary] = []
        for directory in islice(self.sessions_root.iterdir(), SESSION_SCAN_LIMIT):
            if not directory.is_dir() or directory.name == exclude_session_id:
                continue
            try:
                validate_session_id(directory.name)
                expected = session_path(self.sessions_root, directory.name)
                if expected != directory.resolve():
                    continue
                summary = self._read_summary(directory)
            except (OSError, ValueError, SessionStoreError):
                continue
            if summary is not None:
                summaries.append(summary)
        return tuple(sorted(summaries, key=lambda item: item.last_active_at, reverse=True))

    def load(self, session_id: str) -> LoadedSession:
        validate_session_id(session_id)
        directory = session_path(self.sessions_root, session_id)
        path = directory / "conversation.jsonl"
        if path.is_symlink() or path.resolve().parent != directory or not path.is_file():
            raise SessionStoreError(f"Session not found: {session_id}")

        records: list[object] = []
        skipped = 0
        with path.open("rb") as handle:
            for index, raw_line in enumerate(handle):
                try:
                    line = raw_line.decode("utf-8")
                    records.append(decode_record(line))
                except (UnicodeDecodeError, HistoryCodecError) as exc:
                    if index == 0:
                        raise SessionStoreError(f"Invalid session header: {exc}") from exc
                    skipped += 1

        if not records or not isinstance(records[0], SessionRecord):
            raise SessionStoreError("Session header is missing.")
        header = records[0]
        if header.session_id != session_id:
            raise SessionStoreError("Session header ID does not match its directory.")

        messages: list[ConversationMessage] = []
        last_active = header.created_at
        last_record_was_end = False
        active_skill_names: tuple[str, ...] = ()
        for record in records[1:]:
            if isinstance(record, MessageRecord):
                messages.append(decode_message(record.message))
                last_active = max(last_active, record.ts)
                last_record_was_end = False
            elif isinstance(record, SessionEndRecord):
                last_active = max(last_active, record.ts)
                last_record_was_end = True
            elif isinstance(record, SkillStateRecord):
                active_skill_names = record.names
                last_active = max(last_active, record.ts)
                last_record_was_end = False

        repaired, repair_warnings = self._repair_tool_chain(messages)
        conversation = Conversation()
        conversation.restore(repaired)
        warnings = list(repair_warnings)
        if skipped:
            warnings.append(f"Skipped {skipped} invalid journal line(s).")
        if not last_record_was_end:
            warnings.append("The previous KCode process may have exited unexpectedly.")
        metadata = SessionMetadata(
            schema=header.schema_version,
            session_id=header.session_id,
            created_at=header.created_at,
            provider=header.provider,
            model=header.model,
        )
        return LoadedSession(
            metadata=metadata,
            messages=repaired,
            turns=conversation.snapshot(),
            warnings=tuple(warnings),
            last_active_at=last_active,
            skipped_lines=skipped,
            active_skill_names=active_skill_names,
        )

    def _read_summary(self, directory: Path) -> SessionSummary | None:
        path = directory / "conversation.jsonl"
        if path.is_symlink() or path.resolve().parent != directory or not path.is_file():
            return None
        size = path.stat().st_size
        if size <= 0:
            return None
        with path.open("rb") as handle:
            payload = handle.read(LIST_READ_LIMIT + 1)
        lines = payload[:LIST_READ_LIMIT].splitlines()
        if not lines:
            return None
        try:
            header = decode_record(lines[0].decode("utf-8"))
        except (UnicodeDecodeError, HistoryCodecError):
            return None
        if not isinstance(header, SessionRecord) or header.session_id != directory.name:
            return None

        title = ""
        message_count = 0
        last_active = header.created_at
        for raw_line in lines[1:]:
            try:
                record = decode_record(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, HistoryCodecError):
                continue
            if isinstance(record, MessageRecord):
                message_count += 1
                last_active = max(last_active, record.ts)
                if not title and record.message.kind == "user":
                    title = " ".join(record.message.content.split())[:TITLE_LIMIT]
            elif isinstance(record, SessionEndRecord):
                last_active = max(last_active, record.ts)
            elif isinstance(record, SkillStateRecord):
                last_active = max(last_active, record.ts)
        if message_count == 0:
            return None
        return SessionSummary(
            session_id=directory.name,
            title=title or "(untitled session)",
            last_active_at=max(last_active, path.stat().st_mtime),
            provider=header.provider,
            model=header.model,
            size_bytes=size,
            message_count=message_count,
            busy=self._is_busy(directory),
        )

    @staticmethod
    def _is_busy(directory: Path) -> bool:
        lease = SessionLease(directory)
        try:
            lease.acquire()
        except SessionBusyError:
            return True
        finally:
            lease.release()
        return False

    @staticmethod
    def _repair_tool_chain(
        messages: list[ConversationMessage],
    ) -> tuple[tuple[ConversationMessage, ...], tuple[str, ...]]:
        repaired: list[ConversationMessage] = []
        pending: dict[str, str] = {}
        warnings: list[str] = []

        def finish_pending() -> None:
            for call_id, name in pending.items():
                repaired.append(
                    ToolResultMessage(
                        call_id,
                        name,
                        ToolResult.failure(
                            "interrupted",
                            "Tool result is unknown after an interrupted session; verify state.",
                        ),
                    )
                )
                warnings.append(f"Inserted an in-memory unknown result for tool call {call_id}.")
            pending.clear()

        for message in messages:
            if isinstance(message, UserMessage):
                finish_pending()
                repaired.append(message)
            elif isinstance(message, AssistantMessage):
                finish_pending()
                repaired.append(message)
                pending.update((call.id, call.name) for call in message.tool_calls)
            elif isinstance(message, ToolResultMessage):
                if message.tool_call_id not in pending:
                    warnings.append(f"Ignored orphan tool result {message.tool_call_id}.")
                    continue
                repaired.append(message)
                pending.pop(message.tool_call_id, None)
        finish_pending()
        return tuple(repaired), tuple(warnings)
