from __future__ import annotations

import re
import secrets
from datetime import datetime
from pathlib import Path

SESSION_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def create_session_id(now: datetime | None = None) -> str:
    timestamp = now or datetime.now().astimezone()
    return f"{timestamp:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"Invalid KCode session ID: {session_id!r}.")
    return session_id


def session_path(sessions_root: Path, session_id: str) -> Path:
    """Return a bounded session path, rejecting invalid IDs and escaping symlinks."""
    validate_session_id(session_id)
    root = sessions_root.resolve()
    candidate = (root / session_id).resolve()
    if candidate.parent != root:
        raise ValueError("Session path escapes the sessions root.")
    return candidate


def sessions_root_path(workspace_root: Path) -> Path:
    workspace = workspace_root.resolve()
    kcode_dir = workspace / ".kcode"
    raw_candidate = kcode_dir / "sessions"
    if kcode_dir.is_symlink() or raw_candidate.is_symlink():
        raise ValueError("Sessions root must not be a symbolic link.")
    candidate = raw_candidate.resolve()
    if workspace != candidate and workspace not in candidate.parents:
        raise ValueError("Sessions root escapes the workspace.")
    return candidate
