from __future__ import annotations

from collections.abc import Sequence

from kcode.memory.models import (
    PROMPT_BUDGET_BYTES,
    PROMPT_BUDGET_LINES,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    PromptMemoryResult,
)


def render_entry(record: MemoryRecord) -> str:
    return (
        f"- [{record.type.value}] {record.title}: {record.summary} "
        f"Apply: {record.application} (id: {record.id})"
    )


def render_index(records: Sequence[MemoryRecord]) -> str:
    active = sorted(
        (record for record in records if record.status == MemoryStatus.ACTIVE),
        key=lambda record: (-record.updated_at, record.id),
    )
    lines = ["# KCode Long-term Memory", ""]
    lines.extend(render_entry(record) for record in active)
    return "\n".join(lines).rstrip() + "\n"


def _take(
    records: Sequence[MemoryRecord],
    byte_limit: int,
    line_limit: int,
) -> tuple[list[str], list[MemoryRecord]]:
    lines: list[str] = []
    skipped: list[MemoryRecord] = []
    used = 0
    for record in records:
        line = render_entry(record)
        size = len((line + "\n").encode("utf-8"))
        if len(lines) >= line_limit or used + size > byte_limit:
            skipped.append(record)
            continue
        lines.append(line)
        used += size
    return lines, skipped


def render_prompt(
    user_records: Sequence[MemoryRecord],
    project_records: Sequence[MemoryRecord],
) -> PromptMemoryResult:
    user = sorted(
        (r for r in user_records if r.status == MemoryStatus.ACTIVE),
        key=lambda r: (-r.updated_at, r.id),
    )
    project = sorted(
        (r for r in project_records if r.status == MemoryStatus.ACTIVE),
        key=lambda r: (-r.updated_at, r.id),
    )
    if not user and not project:
        return PromptMemoryResult()

    header = (
        "Use only the confirmed long-term memories below. Project memories override "
        "conflicting user memories. Re-check mutable facts before relying on them.\n"
    )
    header_bytes = len(header.encode("utf-8"))
    available_bytes = max(0, PROMPT_BUDGET_BYTES - header_bytes)
    available_lines = max(0, PROMPT_BUDGET_LINES - 1)
    user_quota = available_bytes // 4
    project_quota = available_bytes - user_quota
    user_lines_quota = max(1, available_lines // 4)
    project_lines_quota = max(1, available_lines - user_lines_quota)

    project_lines, project_skipped = _take(project, project_quota, project_lines_quota)
    user_lines, user_skipped = _take(user, user_quota, user_lines_quota)

    used = len("\n".join((*project_lines, *user_lines)).encode("utf-8"))
    used_lines = len(project_lines) + len(user_lines)
    remaining_bytes = max(0, available_bytes - used)
    remaining_lines = max(0, available_lines - used_lines)
    overflow = sorted(
        (*project_skipped, *user_skipped),
        key=lambda r: (r.scope != MemoryScope.PROJECT, -r.updated_at, r.id),
    )
    extra, _ = _take(overflow, remaining_bytes, remaining_lines)

    selected_ids = {
        line.rsplit("(id: ", 1)[-1].removesuffix(")")
        for line in (*project_lines, *user_lines, *extra)
    }
    excluded = len([record for record in (*project, *user) if record.id not in selected_ids])
    sections: list[str] = [header.rstrip()]
    if project_lines:
        sections.extend(("", "Project memories:", *project_lines))
    if user_lines:
        sections.extend(("", "User memories:", *user_lines))
    if extra:
        sections.extend(("", "Additional recent memories:", *extra))
    content = "\n".join(sections).rstrip()
    warnings = (
        (f"{excluded} active memories were excluded from the prompt budget.",)
        if excluded
        else ()
    )
    return PromptMemoryResult(content=content, excluded=excluded, warnings=warnings)
