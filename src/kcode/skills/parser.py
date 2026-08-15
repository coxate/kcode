from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from kcode.skills.models import SkillDefinition, SkillMeta, SkillSource, SkillWarning

MAX_SKILL_BYTES = 32 * 1024
_FRONTMATTER = re.compile(
    r"\A---\r?\n(?P<meta>.*?)\r?\n---(?:\r?\n|\Z)(?P<body>.*)\Z",
    re.DOTALL,
)


def _warning(code: str, source: SkillSource, path: Path, detail: str) -> SkillWarning:
    return SkillWarning(code, source, path.parent.name or "unknown", detail)


def read_skill_bytes(
    path: Path,
    root: Path,
    source: SkillSource,
) -> tuple[bytes | None, SkillWarning | None]:
    try:
        root_resolved = root.resolve(strict=True)
        skill_dir = path.parent
        if skill_dir.is_symlink() or path.is_symlink():
            return None, _warning("symlink", source, path, "symbolic links are not allowed")
        if not skill_dir.is_dir() or not path.is_file():
            return None, _warning("not_file", source, path, "SKILL.md is not a regular file")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root_resolved):
            return None, _warning("path_escape", source, path, "path escapes its skill root")
        size = resolved.stat().st_size
        if size > MAX_SKILL_BYTES:
            return None, _warning("too_large", source, path, "SKILL.md exceeds 32 KiB")
        raw = resolved.read_bytes()
    except (OSError, RuntimeError) as exc:
        return None, _warning("read_failed", source, path, exc.__class__.__name__)
    if b"\x00" in raw:
        return None, _warning("binary", source, path, "binary content is not allowed")
    return raw, None


def parse_skill(
    path: Path,
    root: Path,
    source: SkillSource,
) -> tuple[SkillDefinition | None, SkillWarning | None]:
    raw, warning = read_skill_bytes(path, root, source)
    if raw is None:
        return None, warning
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, _warning("invalid_utf8", source, path, "SKILL.md must be UTF-8")
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
        meta = SkillMeta.model_validate(payload)
    except (yaml.YAMLError, ValidationError, ValueError) as exc:
        return None, _warning("invalid_meta", source, path, exc.__class__.__name__)
    return (
        SkillDefinition(
            meta,
            body,
            source,
            path.resolve(),
            root.resolve(),
            hashlib.sha256(raw).hexdigest(),
        ),
        None,
    )


def render_skill_prompt(definition: SkillDefinition, arguments: str) -> str:
    body = definition.body
    if "$ARGUMENTS" in body:
        body = body.replace("$ARGUMENTS", arguments)
    elif arguments:
        body = f"{body}\n\n## User Request\n\n{arguments}"
    tools = ", ".join(definition.meta.allowed_tools) or "all tools allowed by current permissions"
    return f"## Skill: {definition.meta.name}\n\nSuggested tools: {tools}\n\n{body}"
