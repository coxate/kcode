from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from kcode.hooks.catalog import HookTrustRequest


class HookTrustStore:
    def __init__(self, path: Path | None = None) -> None:
        override = os.environ.get("KCODE_HOOK_TRUST_PATH")
        self.path = path or (
            Path(override) if override else Path.home() / ".kcode" / "hook-trust.json"
        )

    def is_trusted(self, request: HookTrustRequest) -> bool:
        try:
            payload = self._read()
        except (OSError, ValueError):
            return False
        return payload["projects"].get(str(request.project_root)) == request.fingerprint

    def trust(self, request: HookTrustRequest) -> None:
        payload = self._read(allow_missing=True)
        payload["projects"][str(request.project_root)] = request.fingerprint
        self._write(payload)

    def _read(self, *, allow_missing: bool = False) -> dict[str, Any]:
        if self.path.is_symlink():
            raise OSError("hook trust path cannot be a symbolic link")
        if not self.path.exists():
            if allow_missing:
                return {"version": 1, "projects": {}}
            raise FileNotFoundError(self.path)
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or not isinstance(value.get("projects"), dict)
        ):
            raise ValueError("invalid hook trust store")
        return value

    def _write(self, payload: dict[str, Any]) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or self.path.is_symlink():
            raise OSError("unsafe hook trust path")
        if os.name == "posix":
            parent.chmod(0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".hook-trust-", dir=parent)
        temp_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "posix":
                temp_path.chmod(0o600)
            temp_path.replace(self.path)
        finally:
            temp_path.unlink(missing_ok=True)
