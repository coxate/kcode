from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

import yaml
from filelock import FileLock, Timeout
from pydantic import ValidationError

from kcode.memory.models import (
    PROCESSED_HASH_LIMIT,
    MemoryProposal,
    MemoryRecord,
    MemoryScope,
    MemorySnapshot,
    MemoryState,
    MemoryStatus,
)
from kcode.memory.paths import MemoryPathError, build_paths, validate_child
from kcode.memory.prompting import render_index


class MemoryStoreError(RuntimeError):
    pass


class MemoryStoreBusy(MemoryStoreError):
    pass


def _tighten(path: Path, mode: int) -> None:
    if os.name == "posix":
        path.chmod(mode)


class MemoryStore:
    def __init__(
        self,
        scope: MemoryScope,
        workspace: Path,
        *,
        home: Path | None = None,
        sensitive_values: Sequence[str] = (),
    ) -> None:
        self.scope = scope
        self.workspace = workspace.resolve()
        self.paths = build_paths(scope, self.workspace, home)
        self.sensitive_values = tuple(
            sorted((value for value in sensitive_values if value), key=len, reverse=True)
        )

    def update_sensitive_values(self, values: Sequence[str]) -> None:
        self.sensitive_values = tuple(
            sorted((value for value in values if value), key=len, reverse=True)
        )

    def load(self) -> MemorySnapshot:
        self._ensure_directories()
        warnings: list[str] = []
        records: list[MemoryRecord] = []
        proposals: list[MemoryProposal] = []
        for path in sorted(self.paths.entries.glob("*.md")):
            try:
                records.append(self._read_record(path))
            except (OSError, UnicodeError, ValueError, yaml.YAMLError, ValidationError) as exc:
                warnings.append(f"Ignored invalid memory {path.name}: {exc}")
        for path in sorted(self.paths.proposals.glob("*.json")):
            try:
                proposals.append(MemoryProposal.model_validate_json(path.read_text("utf-8")))
            except (OSError, UnicodeError, ValueError, ValidationError) as exc:
                warnings.append(f"Ignored invalid memory proposal {path.name}: {exc}")
        state = self._read_state(warnings)
        try:
            expected = render_index(records)
            current = self.paths.index.read_text("utf-8") if self.paths.index.is_file() else ""
            if current != expected:
                self._write_atomic(self.paths.index, expected)
        except (OSError, MemoryStoreError) as exc:
            warnings.append(f"Could not rebuild {self.scope.value} memory index: {exc}")
        return MemorySnapshot(
            records=tuple(records),
            proposals=tuple(sorted(proposals, key=lambda item: (item.created_at, item.id))),
            state=state,
            warnings=tuple(warnings),
        )

    def save(self, record: MemoryRecord) -> None:
        if record.scope != self.scope:
            raise MemoryStoreError("Record scope does not match this store.")
        with self._locked():
            target = self.paths.entries / f"{record.id}.md"
            self._write_atomic(target, self._encode_record(record))
            self.rebuild_index(locked=True)

    def save_proposal(self, proposal: MemoryProposal) -> bool:
        if proposal.scope != self.scope:
            raise MemoryStoreError("Proposal scope does not match this store.")
        with self._locked():
            state = self._read_state([])
            if proposal.id in state.processed_proposal_hashes:
                return False
            target = self.paths.proposals / f"{proposal.id}.json"
            if target.exists():
                return False
            self._write_atomic(target, proposal.model_dump_json(indent=2, by_alias=True) + "\n")
            return True

    def pending(self) -> tuple[MemoryProposal, ...]:
        return self.load().proposals

    def get(self, memory_id: str) -> MemoryRecord:
        return self._read_record(self._record_path(memory_id))

    def set_status(self, memory_id: str, status: MemoryStatus) -> MemoryRecord:
        with self._locked():
            record = self._read_record(self._record_path(memory_id))
            updated = record.model_copy(
                update={"status": status, "updated_at": __import__("time").time()}
            )
            self._write_atomic(self._record_path(memory_id), self._encode_record(updated))
            self.rebuild_index(locked=True)
            return updated

    def delete(self, memory_id: str) -> None:
        with self._locked():
            path = self._record_path(memory_id)
            if not path.is_file():
                raise MemoryStoreError(f"Memory does not exist: {memory_id}")
            path.unlink()
            self.rebuild_index(locked=True)

    def resolve_proposal(self, proposal_id: str) -> None:
        with self._locked():
            path = self.paths.proposals / f"{proposal_id}.json"
            validate_child(self.paths.root, path)
            if path.exists():
                path.unlink()
            state = self._read_state([])
            processed = (*state.processed_proposal_hashes, proposal_id)[-PROCESSED_HASH_LIMIT:]
            self._write_state(state.model_copy(update={"processed_proposal_hashes": processed}))

    def rebuild_index(self, *, locked: bool = False) -> str:
        if not locked:
            with self._locked():
                return self.rebuild_index(locked=True)
        records: list[MemoryRecord] = []
        for path in sorted(self.paths.entries.glob("*.md")):
            try:
                records.append(self._read_record(path))
            except (OSError, UnicodeError, ValueError, yaml.YAMLError, ValidationError):
                continue
        content = render_index(records)
        self._write_atomic(self.paths.index, content)
        return content

    def load_state(self) -> MemoryState:
        self._ensure_directories()
        return self._read_state([])

    def save_state(self, state: MemoryState) -> None:
        with self._locked():
            self._write_state(state)

    def _record_path(self, memory_id: str) -> Path:
        invalid_hex = any(ch not in "0123456789abcdef" for ch in memory_id[4:])
        if not memory_id.startswith("mem_") or len(memory_id) != 36 or invalid_hex:
            raise MemoryStoreError(f"Invalid memory id: {memory_id}")
        path = self.paths.entries / f"{memory_id}.md"
        validate_child(self.paths.root, path)
        return path

    def _encode_record(self, record: MemoryRecord) -> str:
        values = record.model_dump(mode="json", by_alias=True)
        body = values.pop("body")
        metadata = yaml.safe_dump(values, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{metadata}\n---\n{body.rstrip()}\n"

    def _read_record(self, path: Path) -> MemoryRecord:
        validate_child(self.paths.root, path)
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---\n"):
            raise ValueError("missing YAML frontmatter")
        try:
            metadata_text, body = raw[4:].split("\n---\n", 1)
        except ValueError as exc:
            raise ValueError("unterminated YAML frontmatter") from exc
        metadata = yaml.safe_load(metadata_text)
        if not isinstance(metadata, dict):
            raise ValueError("frontmatter must be a mapping")
        record = MemoryRecord.model_validate({**metadata, "body": body.rstrip("\n")})
        if record.scope != self.scope or path.stem != record.id:
            raise ValueError("record scope or filename does not match metadata")
        return record

    def _read_state(self, warnings: list[str]) -> MemoryState:
        if not self.paths.state.exists():
            return MemoryState()
        try:
            validate_child(self.paths.root, self.paths.state)
            return MemoryState.model_validate_json(self.paths.state.read_text("utf-8"))
        except (OSError, UnicodeError, ValueError, ValidationError, MemoryPathError) as exc:
            warnings.append(f"Ignored invalid {self.scope.value} memory state: {exc}")
            return MemoryState()

    def _write_state(self, state: MemoryState) -> None:
        self._write_atomic(
            self.paths.state,
            state.model_dump_json(indent=2, by_alias=True) + "\n",
        )

    def _redact(self, content: str) -> str:
        for value in self.sensitive_values:
            content = content.replace(value, "[REDACTED]")
        return content

    def _ensure_directories(self) -> None:
        for existing in (self.paths.root, self.paths.entries, self.paths.proposals):
            if existing.is_symlink():
                raise MemoryStoreError(f"Memory directory cannot be a symbolic link: {existing}")
        self.paths.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.paths.entries.mkdir(exist_ok=True, mode=0o700)
        self.paths.proposals.mkdir(exist_ok=True, mode=0o700)
        for directory in (self.paths.root, self.paths.entries, self.paths.proposals):
            _tighten(directory, 0o700)

    def _write_atomic(self, target: Path, content: str) -> None:
        self._ensure_directories()
        validate_child(self.paths.root, target)
        if target.is_symlink():
            raise MemoryStoreError(f"Refusing to replace symbolic link: {target}")
        descriptor, raw = tempfile.mkstemp(prefix=".memory-", dir=target.parent)
        temporary = Path(raw)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(self._redact(content))
                handle.flush()
                os.fsync(handle.fileno())
            _tighten(temporary, 0o600)
            os.replace(temporary, target)
            _tighten(target, 0o600)
            if hasattr(os, "O_DIRECTORY"):
                directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _locked(self):
        self._ensure_directories()
        validate_child(self.paths.root, self.paths.lock)
        if self.paths.lock.is_symlink():
            raise MemoryStoreError("Memory lock cannot be a symbolic link.")
        lock = FileLock(str(self.paths.lock), thread_local=False)
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise MemoryStoreBusy(f"{self.scope.value} memory is in use") from exc
        try:
            _tighten(self.paths.lock, 0o600)
            yield lock
        finally:
            lock.release()
