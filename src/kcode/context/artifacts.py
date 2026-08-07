from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from kcode.context.models import ArtifactRef

PREVIEW_MAX_BYTES = 2_048
PREVIEW_MAX_LINES = 20
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


class ArtifactError(RuntimeError):
    pass


class ArtifactConflictError(ArtifactError):
    pass


def _safe_component(value: str) -> str:
    if value and value not in {".", ".."} and _SAFE_COMPONENT.fullmatch(value):
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"tool-{digest}"


def _head_preview(content: str) -> str:
    lines = content.splitlines(keepends=True)[:PREVIEW_MAX_LINES]
    encoded = "".join(lines).encode("utf-8")[:PREVIEW_MAX_BYTES]
    return encoded.decode("utf-8", errors="ignore")


class ArtifactStore:
    def __init__(
        self,
        workspace_root: Path,
        session_id: str,
        *,
        sensitive_values: Sequence[str] = (),
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.session_id = _safe_component(session_id)
        self.root = self.workspace_root / ".kcode" / "sessions" / self.session_id / "tool-results"
        self._sensitive_values = tuple(
            sorted((value for value in sensitive_values if value), key=len, reverse=True)
        )
        self._lock = threading.RLock()
        self._refs: dict[str, ArtifactRef] = {}

    def redact(self, content: str) -> str:
        redacted = content
        for value in self._sensitive_values:
            redacted = redacted.replace(value, "[REDACTED]")
        return redacted

    def update_sensitive_values(self, values: Sequence[str]) -> None:
        with self._lock:
            self._sensitive_values = tuple(
                sorted((value for value in values if value), key=len, reverse=True)
            )

    def path_for(self, tool_use_id: str) -> Path:
        candidate = (self.root / _safe_component(tool_use_id)).resolve()
        if candidate.parent != self.root.resolve():
            raise ArtifactError("Artifact path escapes the current session directory.")
        return candidate

    def contains_path(self, path: str) -> bool:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve()
        root = self.root.resolve()
        return resolved == root or root in resolved.parents

    async def store(
        self,
        tool_use_id: str,
        tool_name: str,
        content: str,
        *,
        status: str = "success",
    ) -> ArtifactRef:
        redacted = self.redact(content)
        return await asyncio.to_thread(
            self._store_sync,
            tool_use_id,
            tool_name,
            redacted,
            status,
        )

    def _store_sync(
        self,
        tool_use_id: str,
        tool_name: str,
        content: str,
        status: str,
    ) -> ArtifactRef:
        payload = content.encode("utf-8")
        with self._lock:
            known = self._refs.get(tool_use_id)
            if known is not None:
                existing = self.path_for(tool_use_id).read_bytes()
                if existing != payload:
                    raise ArtifactConflictError(
                        f"Artifact {tool_use_id!r} already exists with different content."
                    )
                return known

            target = self.path_for(tool_use_id)
            self.root.mkdir(parents=True, exist_ok=True)
            if target.exists():
                existing = target.read_bytes()
                if existing != payload:
                    raise ArtifactConflictError(
                        f"Artifact {tool_use_id!r} already exists with different content."
                    )
            else:
                descriptor, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=self.root)
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(temporary, 0o600)
                    os.replace(temporary, target)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise

            stat = target.stat()
            created_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            reference = ArtifactRef(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                path=target.relative_to(self.workspace_root).as_posix(),
                byte_count=len(payload),
                status=status,
                created_at=created_at,
                redacted=True,
            )
            self._refs[tool_use_id] = reference
            return reference

    async def read_range(
        self,
        artifact: ArtifactRef | str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> str:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if length is not None and length < 0:
            raise ValueError("length must be non-negative")
        tool_use_id = artifact.tool_use_id if isinstance(artifact, ArtifactRef) else artifact
        return await asyncio.to_thread(self._read_range_sync, tool_use_id, offset, length)

    def _read_range_sync(self, tool_use_id: str, offset: int, length: int | None) -> str:
        target = self.path_for(tool_use_id)
        with target.open("rb") as handle:
            handle.seek(offset)
            payload = handle.read() if length is None else handle.read(length)
        return payload.decode("utf-8", errors="replace")

    def build_preview(self, reference: ArtifactRef, content: str) -> str:
        preview = _head_preview(self.redact(content))
        return (
            "[KCode Artifact preview; this is not the complete tool result]\n"
            f"tool: {reference.tool_name}\n"
            f"tool_use_id: {reference.tool_use_id}\n"
            f"status: {reference.status}\n"
            f"original_bytes: {reference.byte_count}\n"
            f"artifact: {reference.path}\n"
            "preview:\n"
            f"{preview}\n"
            "需要完整或精确内容时，请按范围重新读取上述 Artifact；不要依据预览补全。"
        )
