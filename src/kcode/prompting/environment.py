from __future__ import annotations

import asyncio
import platform
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

from kcode.conversation import EnvironmentMessage

GIT_OUTPUT_LIMIT = 64 * 1024


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    working_directory: str
    platform: str
    current_date: str
    git_status: str
    app_version: str
    model: str

    def render(self) -> str:
        fields = (
            ("Working directory", self.working_directory),
            ("Platform", self.platform),
            ("Date", self.current_date),
            ("Git", self.git_status),
            ("KCode version", self.app_version),
            ("Model", self.model),
        )
        lines = ["<environment_context>"]
        lines.extend(f"{label}: {escape(value)}" for label, value in fields)
        lines.append("</environment_context>")
        return "\n".join(lines)


class EnvironmentCollector:
    def __init__(self, git_timeout_seconds: float = 0.5) -> None:
        self.git_timeout_seconds = git_timeout_seconds

    async def collect(
        self,
        cwd: Path,
        *,
        app_version: str,
        model: str,
    ) -> EnvironmentMessage:
        try:
            platform_name = (
                " ".join(part for part in (platform.system(), platform.machine()) if part)
                or "unavailable"
            )
        except Exception:
            platform_name = "unavailable"
        try:
            current_date = date.today().isoformat()
        except Exception:
            current_date = "unavailable"
        git_status = await self._git_status(cwd)
        snapshot = EnvironmentSnapshot(
            str(cwd.resolve()),
            platform_name,
            current_date,
            git_status,
            app_version,
            model,
        )
        return EnvironmentMessage(snapshot.render())

    async def _git_status(self, cwd: Path) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "status",
                "--porcelain=v1",
                "--branch",
                "--untracked-files=no",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError):
            return "unavailable"
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.git_timeout_seconds
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return "unavailable"
        if len(stdout) > GIT_OUTPUT_LIMIT or len(stderr) > GIT_OUTPUT_LIMIT:
            return "unavailable"
        if process.returncode != 0:
            lowered = stderr.lower()
            if b"not a git repository" in lowered:
                return "not a repository"
            return "unavailable"
        try:
            lines = stdout.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return "unavailable"
        if not lines or not lines[0].startswith("## "):
            return "unavailable"
        branch_line = lines[0][3:]
        dirty = bool(lines[1:])
        if branch_line.startswith("HEAD (no branch)") or branch_line.startswith("HEAD "):
            branch = "detached"
        else:
            branch = branch_line.split("...", 1)[0].strip()
        if not branch:
            return "unavailable"
        return f"{branch} ({'dirty' if dirty else 'clean'})"
