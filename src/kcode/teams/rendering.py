from __future__ import annotations

from collections.abc import Iterable

MAX_TEAM_BYTES = 32 * 1024


def redact(value: str, sensitive_values: tuple[str, ...]) -> str:
    for secret in sensitive_values:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def truncate(value: str, limit: int = MAX_TEAM_BYTES) -> tuple[str, bool]:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value, False
    suffix = "\n[truncated]"
    suffix_raw = suffix.encode("utf-8")
    body = raw[: max(0, limit - len(suffix_raw))].decode("utf-8", errors="ignore")
    return body + suffix, True


def protected_result(
    body: str,
    tail: str,
    sensitive_values: tuple[str, ...],
    limit: int = MAX_TEAM_BYTES,
) -> tuple[str, bool]:
    body = redact(body, sensitive_values)
    tail = redact(tail, sensitive_values)
    tail_raw = tail.encode("utf-8")
    if len(tail_raw) >= limit:
        return truncate(tail, limit)
    separator = "\n\n" if body and tail else ""
    body_budget = limit - len(tail_raw) - len(separator.encode("utf-8"))
    rendered_body, truncated = truncate(body, body_budget)
    return f"{rendered_body}{separator}{tail}", truncated


def join_lines(lines: Iterable[str], sensitive_values: tuple[str, ...]) -> str:
    rendered, _ = truncate(redact("\n".join(lines), sensitive_values))
    return rendered
