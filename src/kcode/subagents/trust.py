from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kcode.subagents.models import AgentSource
from kcode.subagents.parser import read_agent_bytes


@dataclass(frozen=True, slots=True)
class AgentTrustRequest:
    project_root: Path
    fingerprint: str
    agent_names: tuple[str, ...]


def project_fingerprint(
    project_root: Path,
    files: tuple[Path, ...],
) -> tuple[AgentTrustRequest | None, tuple[str, ...]]:
    project = project_root.resolve()
    root = project / ".kcode" / "agents"
    digest = hashlib.sha256(str(project).encode("utf-8"))
    items: list[tuple[str, bytes]] = []
    names: list[str] = []
    warnings: list[str] = []
    for path in sorted(files, key=lambda item: item.as_posix()):
        raw, warning = read_agent_bytes(path, root, AgentSource.PROJECT)
        if raw is None:
            if warning is not None:
                warnings.append(warning.render())
            continue
        relative = path.resolve().relative_to(project).as_posix()
        items.append((relative, raw))
        names.append(path.stem)
    if not items:
        return None, tuple(warnings)
    for relative, raw in items:
        digest.update(b"\0path\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0content\0")
        digest.update(raw)
    return AgentTrustRequest(project, digest.hexdigest(), tuple(names)), tuple(warnings)


class AgentTrustStore:
    def __init__(self, path: Path | None = None) -> None:
        override = os.environ.get("KCODE_SUBAGENT_TRUST_PATH")
        self.path = path or (
            Path(override) if override else Path.home() / ".kcode" / "subagent-trust.json"
        )

    def is_trusted(self, request: AgentTrustRequest) -> bool:
        try:
            payload = self._read()
        except (OSError, ValueError):
            return False
        return payload.get("projects", {}).get(str(request.project_root)) == request.fingerprint

    def trust(self, request: AgentTrustRequest) -> None:
        payload = self._read(allow_missing=True)
        projects = payload.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise ValueError("invalid SubAgent trust store")
        projects[str(request.project_root)] = request.fingerprint
        self._write(payload)

    def _read(self, *, allow_missing: bool = False) -> dict[str, Any]:
        if self.path.is_symlink():
            raise OSError("SubAgent trust path cannot be a symlink")
        if not self.path.exists():
            if allow_missing:
                return {"version": 1, "projects": {}}
            raise FileNotFoundError(self.path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(payload.get("projects"), dict)
        ):
            raise ValueError("invalid SubAgent trust store")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or self.path.is_symlink():
            raise OSError("unsafe SubAgent trust path")
        if os.name == "posix":
            parent.chmod(0o700)
        descriptor, raw_path = tempfile.mkstemp(prefix=".subagent-trust-", dir=parent)
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "posix":
                temporary.chmod(0o600)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
