from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from kcode.subagents.models import AgentDefinition, AgentMeta, AgentSource, AgentWarning

MAX_AGENT_BYTES = 32 * 1024
_FRONTMATTER = re.compile(
    r"\A---\r?\n(?P<meta>.*?)\r?\n---(?:\r?\n|\Z)(?P<body>.*)\Z",
    re.DOTALL,
)


def _warning(code: str, source: AgentSource, path: Path, detail: str) -> AgentWarning:
    return AgentWarning(code, source, path.stem or "unknown", detail)


def read_agent_bytes(
    path: Path,
    root: Path,
    source: AgentSource,
) -> tuple[bytes | None, AgentWarning | None]:
    try:
        root_resolved = root.resolve(strict=True)
        if path.is_symlink() or not path.is_file():
            return None, _warning("not_file", source, path, "regular files are required")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root_resolved):
            return None, _warning("path_escape", source, path, "path escapes its Agent root")
        if resolved.stat().st_size > MAX_AGENT_BYTES:
            return None, _warning("too_large", source, path, "Agent definition exceeds 32 KiB")
        raw = resolved.read_bytes()
    except (OSError, RuntimeError) as exc:
        return None, _warning("read_failed", source, path, exc.__class__.__name__)
    if b"\x00" in raw:
        return None, _warning("binary", source, path, "binary content is not allowed")
    return raw, None


def parse_agent(
    path: Path,
    root: Path,
    source: AgentSource,
) -> tuple[AgentDefinition | None, AgentWarning | None]:
    raw, warning = read_agent_bytes(path, root, source)
    if raw is None:
        return None, warning
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, _warning("invalid_utf8", source, path, "definition must be UTF-8")
    match = _FRONTMATTER.fullmatch(text)
    if match is None:
        return None, _warning("frontmatter", source, path, "missing strict YAML frontmatter")
    body = match.group("body").strip()
    if not body:
        return None, _warning("empty_body", source, path, "Markdown body cannot be empty")
    try:
        payload = yaml.safe_load(match.group("meta"))
        if not isinstance(payload, dict):
            raise ValueError("frontmatter must be a mapping")
        meta = AgentMeta.model_validate(payload)
    except (yaml.YAMLError, ValidationError, ValueError) as exc:
        return None, _warning("invalid_meta", source, path, exc.__class__.__name__)
    return (
        AgentDefinition(
            meta,
            body,
            source,
            path.resolve(),
            root.resolve(),
            hashlib.sha256(raw).hexdigest(),
        ),
        None,
    )
