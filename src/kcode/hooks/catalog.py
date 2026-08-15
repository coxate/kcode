from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from kcode.hooks.models import Hook, HookCatalog, HookSource, HookWarning
from kcode.hooks.parser import parse_hook

MAX_CONFIG_BYTES = 256 * 1024
MAX_HOOKS = 100


@dataclass(frozen=True, slots=True)
class HookTrustRequest:
    project_root: Path
    config_path: Path
    fingerprint: str
    hook_ids: tuple[str, ...]


def read_hook_bytes(path: Path, root: Path) -> tuple[bytes | None, HookWarning | None]:
    try:
        if path.is_symlink():
            raise ValueError("configuration path cannot be a symbolic link")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve()):
            raise ValueError("configuration path escapes its expected directory")
        if not resolved.is_file():
            raise ValueError("configuration path is not a regular file")
        if resolved.stat().st_size > MAX_CONFIG_BYTES:
            raise ValueError("configuration exceeds 256 KiB")
        raw = resolved.read_bytes()
        if b"\0" in raw:
            raise ValueError("configuration appears to be binary")
        raw.decode("utf-8")
        return raw, None
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, HookWarning("invalid_file", f"ignored {path}: {exc}")


def _document(raw: bytes, path: Path) -> tuple[list[object] | None, HookWarning | None]:
    try:
        payload = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None, HookWarning("invalid_yaml", f"ignored {path}: invalid YAML")
    if not isinstance(payload, dict) or set(payload) != {"hooks"}:
        return None, HookWarning("invalid_document", f"ignored {path}: expected only a hooks array")
    hooks = payload.get("hooks")
    if not isinstance(hooks, list):
        return None, HookWarning("invalid_document", f"ignored {path}: hooks must be an array")
    return hooks, None


class HookCatalogBuilder:
    def __init__(
        self,
        project_root: Path,
        *,
        user_path: Path | None = None,
        project_path: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.user_path = user_path or Path.home() / ".kcode" / "hooks.yaml"
        self.project_path = project_path or self.project_root / ".kcode" / "hooks.yaml"

    def trust_request(self) -> tuple[HookTrustRequest | None, tuple[HookWarning, ...]]:
        raw, warning = read_hook_bytes(self.project_path, self.project_root)
        if raw is None:
            return None, (warning,) if warning is not None else ()
        digest = hashlib.sha256()
        digest.update(str(self.project_root).encode("utf-8"))
        digest.update(b"\0path\0.kcode/hooks.yaml\0content\0")
        digest.update(raw)
        hooks, document_warning = _document(raw, self.project_path)
        ids: list[str] = []
        if hooks is not None:
            for item in hooks:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ids.append(item["id"][:64])
        warnings = tuple(item for item in (warning, document_warning) if item is not None)
        return (
            HookTrustRequest(
                self.project_root,
                self.project_path.resolve(),
                digest.hexdigest(),
                tuple(ids[:MAX_HOOKS]),
            ),
            warnings,
        )

    def build(self, *, project_trusted: bool) -> HookCatalog:
        hooks: list[Hook] = []
        warnings: list[HookWarning] = []
        sources: list[Path] = []
        seen: set[str] = set()
        layers = [(HookSource.USER, self.user_path, self.user_path.parent)]
        if project_trusted:
            layers.append((HookSource.PROJECT, self.project_path, self.project_root))
        for source, path, root in layers:
            raw, warning = read_hook_bytes(path, root)
            if warning is not None:
                warnings.append(warning)
            if raw is None:
                continue
            items, warning = _document(raw, path)
            if warning is not None:
                warnings.append(warning)
            if items is None:
                continue
            sources.append(path.resolve())
            for value in items:
                hook, parse_warning = parse_hook(value, source, path.resolve(), len(hooks))
                if parse_warning is not None:
                    warnings.append(parse_warning)
                    continue
                assert hook is not None
                if hook.id in seen:
                    warnings.append(
                        HookWarning("duplicate_id", "duplicate id was skipped", hook.id, hook.event)
                    )
                    continue
                if len(hooks) >= MAX_HOOKS:
                    warnings.append(HookWarning("hook_limit", "at most 100 Hooks are loaded"))
                    continue
                seen.add(hook.id)
                hooks.append(hook)
        return HookCatalog(tuple(hooks), tuple(sources), tuple(warnings))
