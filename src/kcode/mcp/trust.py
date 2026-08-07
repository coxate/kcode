from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kcode.config import McpServerConfig

TRUST_VERSION = 1


@dataclass(frozen=True, slots=True)
class McpTrustRequest:
    project_root: Path
    server_name: str
    server_type: str
    target: str
    environment_variables: tuple[str, ...]
    fingerprint: str


def _raw_server_value(server: McpServerConfig) -> dict[str, Any]:
    return server.model_dump(mode="json", exclude={"source"}, exclude_none=True)


def trust_fingerprint(project_root: Path, server: McpServerConfig) -> str:
    payload = json.dumps(
        {
            "project": str(project_root.resolve()),
            "server": server.name,
            "config": _raw_server_value(server),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class McpTrustStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.home() / ".kcode" / "mcp-trust.json"
        self._lock = threading.RLock()
        self.warnings: list[str] = []

    def _read(self) -> dict[str, list[str]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != TRUST_VERSION:
                raise ValueError("unsupported trust store")
            projects = raw.get("projects")
            if not isinstance(projects, dict):
                raise ValueError("invalid projects")
            result: dict[str, list[str]] = {}
            for project, values in projects.items():
                if not isinstance(project, str) or not isinstance(values, list):
                    raise ValueError("invalid project entry")
                if not all(isinstance(value, str) for value in values):
                    raise ValueError("invalid fingerprint")
                result[project] = list(dict.fromkeys(values))
            return result
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            warning = f"KCode ignored invalid MCP trust settings at {self.path}."
            if warning not in self.warnings:
                self.warnings.append(warning)
            return {}

    def _write(self, projects: dict[str, list[str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".mcp-trust-", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(raw_path)
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                descriptor = -1
                json.dump(
                    {"version": TRUST_VERSION, "projects": projects},
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _project_key(project_root: Path) -> str:
        return hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()

    def is_trusted(self, project_root: Path, fingerprint: str) -> bool:
        with self._lock:
            return fingerprint in self._read().get(self._project_key(project_root), ())

    def trust(self, project_root: Path, fingerprint: str) -> None:
        with self._lock:
            projects = self._read()
            key = self._project_key(project_root)
            values = projects.setdefault(key, [])
            if fingerprint not in values:
                values.append(fingerprint)
                self._write(projects)

    def clear_project(self, project_root: Path) -> bool:
        with self._lock:
            projects = self._read()
            removed = projects.pop(self._project_key(project_root), None) is not None
            if removed:
                self._write(projects)
            return removed
