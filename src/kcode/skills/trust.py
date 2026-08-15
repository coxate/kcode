from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kcode.skills.models import SkillSource
from kcode.skills.parser import read_skill_bytes


@dataclass(frozen=True, slots=True)
class SkillTrustRequest:
    project_root: Path
    fingerprint: str
    skill_names: tuple[str, ...]


def project_fingerprint(
    project_root: Path, skill_files: tuple[Path, ...]
) -> tuple[SkillTrustRequest | None, tuple[str, ...]]:
    project = project_root.resolve()
    digest = hashlib.sha256()
    digest.update(str(project).encode("utf-8"))
    names: list[str] = []
    warnings: list[str] = []
    items: list[tuple[str, bytes]] = []
    skill_root = project / ".kcode" / "skills"
    for path in sorted(skill_files, key=lambda item: item.as_posix()):
        raw, warning = read_skill_bytes(path, skill_root, SkillSource.PROJECT)
        if raw is None:
            if warning is not None:
                warnings.append(warning.render())
            continue
        relative = path.resolve().relative_to(project).as_posix()
        items.append((relative, raw))
        names.append(path.parent.name)
    if not items:
        return None, tuple(warnings)
    for relative, raw in items:
        digest.update(b"\0path\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0content\0")
        digest.update(raw)
    return SkillTrustRequest(project, digest.hexdigest(), tuple(names)), tuple(warnings)


class SkillTrustStore:
    def __init__(self, path: Path | None = None) -> None:
        override = os.environ.get("KCODE_SKILL_TRUST_PATH")
        default = Path(override) if override else Path.home() / ".kcode" / "skill-trust.json"
        self.path = path or default

    def is_trusted(self, request: SkillTrustRequest) -> bool:
        try:
            payload = self._read()
        except (OSError, ValueError):
            return False
        return payload.get("projects", {}).get(str(request.project_root)) == request.fingerprint

    def trust(self, request: SkillTrustRequest) -> None:
        payload = self._read(allow_missing=True)
        projects = payload.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise ValueError("invalid skill trust store")
        projects[str(request.project_root)] = request.fingerprint
        self._write(payload)

    def _read(self, *, allow_missing: bool = False) -> dict[str, Any]:
        if self.path.is_symlink():
            raise OSError("skill trust path cannot be a symlink")
        if not self.path.exists():
            if allow_missing:
                return {"version": 1, "projects": {}}
            raise FileNotFoundError(self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("invalid skill trust store")
        if not isinstance(raw.get("projects"), dict):
            raise ValueError("invalid skill trust projects")
        return raw

    def _write(self, payload: dict[str, Any]) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or self.path.is_symlink():
            raise OSError("unsafe skill trust path")
        if os.name == "posix":
            parent.chmod(0o700)
        fd, temporary = tempfile.mkstemp(prefix=".skill-trust-", dir=parent)
        temp_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "posix":
                temp_path.chmod(0o600)
            temp_path.replace(self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
