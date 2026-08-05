from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

from kcode.permissions.models import (
    LayerName,
    PermissionLayer,
    PermissionMode,
    PermissionPersistenceError,
    PermissionSettings,
)
from kcode.permissions.rules import parse_rules

ALLOWED_KEYS = {"defaultMode", "allow", "deny"}


def default_permission_paths(cwd: Path | None = None) -> tuple[Path, Path, Path]:
    project_root = (cwd or Path.cwd()).resolve()
    return (
        Path.home() / ".kcode" / "permissions.yaml",
        project_root / ".kcode" / "permissions.yaml",
        project_root / ".kcode" / "permissions.local.yaml",
    )


def empty_permission_settings(
    workspace_root: Path, initial_mode: PermissionMode = PermissionMode.DEFAULT
) -> PermissionSettings:
    user, project, local = default_permission_paths(workspace_root)
    return PermissionSettings(
        (
            PermissionLayer("local", local),
            PermissionLayer("project", project),
            PermissionLayer("user", user),
        ),
        initial_mode,
    )


def _empty_layer(name: LayerName, path: Path) -> PermissionLayer:
    return PermissionLayer(name, path)


def _read_mapping(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("the document root must be a mapping")
    unknown = set(value) - ALLOWED_KEYS
    if unknown:
        raise ValueError("unknown top-level fields")
    return value


def _parse_layer(name: LayerName, path: Path, raw: dict[str, Any]) -> PermissionLayer:
    default_raw = raw.get("defaultMode")
    default_mode = None if default_raw is None else PermissionMode(default_raw)
    allow_raw = raw.get("allow", [])
    deny_raw = raw.get("deny", [])
    if not isinstance(allow_raw, list) or not isinstance(deny_raw, list):
        raise ValueError("allow and deny must be lists")
    if not all(isinstance(item, str) for item in (*allow_raw, *deny_raw)):
        raise ValueError("allow and deny entries must be strings")
    return PermissionLayer(
        name,
        path,
        default_mode,
        parse_rules(allow_raw),
        parse_rules(deny_raw),
    )


def load_permission_layer(name: LayerName, path: Path) -> PermissionLayer:
    if not path.is_file():
        return _empty_layer(name, path)
    return _parse_layer(name, path, _read_mapping(path))


class PermissionConfigLoader:
    def load(self, user_path: Path, project_path: Path, local_path: Path) -> PermissionSettings:
        layers: list[PermissionLayer] = []
        warnings: list[str] = []
        for name, path in (
            ("local", local_path),
            ("project", project_path),
            ("user", user_path),
        ):
            try:
                layer = load_permission_layer(name, path)  # type: ignore[arg-type]
            except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                layer = _empty_layer(name, path)  # type: ignore[arg-type]
                warnings.append(f"KCode ignored invalid {name} permission settings at {path}.")
            layers.append(layer)
        initial_mode = next(
            (layer.default_mode for layer in layers if layer.default_mode is not None),
            PermissionMode.DEFAULT,
        )
        return PermissionSettings(tuple(layers), initial_mode, tuple(warnings))


class LocalPermissionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append_allow(self, rule: str) -> PermissionLayer:
        with self._lock:
            try:
                raw = _read_mapping(self.path) if self.path.exists() else {}
                layer = _parse_layer("local", self.path, raw)
                if any(item.raw == rule for item in layer.allow):
                    return layer
                allow = list(raw.get("allow", []))
                allow.append(rule)
                updated = dict(raw)
                updated["allow"] = allow
                self._atomic_write(updated)
                return _parse_layer("local", self.path, updated)
            except PermissionPersistenceError:
                raise
            except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
                raise PermissionPersistenceError(
                    "The local permission rule could not be saved safely."
                ) from exc

    def _atomic_write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".permissions-", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(raw_path)
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                descriptor = -1
                yaml.safe_dump(value, handle, allow_unicode=True, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise PermissionPersistenceError(
                "The local permission rule could not be saved safely."
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
