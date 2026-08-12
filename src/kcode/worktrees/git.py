from __future__ import annotations

import asyncio
import os
from pathlib import Path

from kcode.worktrees.models import (
    GitWorktreeEntry,
    GitWorktreeError,
    GitWorktreeState,
    RepositoryInfo,
    WorktreeError,
    validate_sha,
)

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_STREAM_BYTES = 64 * 1024


def _validate_git_sha(value: str) -> str:
    try:
        return validate_sha(value)
    except WorktreeError as exc:
        raise GitWorktreeError(exc.code, str(exc)) from exc


def parse_worktree_porcelain(data: bytes) -> tuple[GitWorktreeEntry, ...]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise GitWorktreeError("invalid_git_output", "Git Worktree 列表不是有效 UTF-8。") from exc
    if not text or not text.endswith("\0\0"):
        raise GitWorktreeError("invalid_git_output", "Git Worktree 列表不完整。")
    records: list[GitWorktreeEntry] = []
    fields: dict[str, str] = {}

    def finish() -> None:
        nonlocal fields
        if not fields:
            return
        if "worktree" not in fields:
            raise GitWorktreeError("invalid_git_output", "Git Worktree 记录缺少路径。")
        bare = "bare" in fields
        if not bare and "HEAD" not in fields:
            raise GitWorktreeError("invalid_git_output", "Git Worktree 记录缺少 HEAD。")
        head = _validate_git_sha(fields["HEAD"]) if "HEAD" in fields else None
        branch = fields.get("branch")
        if branch is not None:
            prefix = "refs/heads/"
            if not branch.startswith(prefix) or branch == prefix:
                raise GitWorktreeError("invalid_git_output", "Git Worktree 分支字段无效。")
            branch = branch[len(prefix) :]
        records.append(
            GitWorktreeEntry(
                Path(fields["worktree"]).resolve(strict=False),
                head,
                branch,
                detached="detached" in fields,
                bare=bare,
                prunable="prunable" in fields,
            )
        )
        fields = {}

    for token in text.split("\0"):
        if not token:
            finish()
            continue
        key, separator, value = token.partition(" ")
        if key not in {"worktree", "HEAD", "branch", "bare", "detached", "locked", "prunable"}:
            raise GitWorktreeError("invalid_git_output", f"Git Worktree 包含未知字段：{key}。")
        if key in fields:
            raise GitWorktreeError("invalid_git_output", f"Git Worktree 重复字段：{key}。")
        if key in {"worktree", "HEAD", "branch"} and not separator:
            raise GitWorktreeError("invalid_git_output", f"Git Worktree 字段缺少值：{key}。")
        fields[key] = value
    if fields:
        raise GitWorktreeError("invalid_git_output", "Git Worktree 列表缺少记录终止符。")
    return tuple(records)


class GitWorktreeClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_stream_bytes: int = MAX_STREAM_BYTES,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_stream_bytes = max_stream_bytes

    async def _drain(self, stream: asyncio.StreamReader | None) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        chunks: list[bytes] = []
        size = 0
        overflow = False
        while chunk := await stream.read(8192):
            remaining = self.max_stream_bytes - size
            if remaining > 0:
                chunks.append(chunk[:remaining])
                size += min(len(chunk), remaining)
            if len(chunk) > remaining:
                overflow = True
        return b"".join(chunks), overflow

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            await process.wait()
            return
        try:
            process.terminate()
        except ProcessLookupError:
            await process.wait()
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def _run(self, cwd: Path, *args: str) -> bytes:
        env = os.environ.copy()
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "",
                "SSH_ASKPASS": "",
                "GCM_INTERACTIVE": "Never",
            }
        )
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            raise GitWorktreeError("git_unavailable", "无法启动 Git。") from exc
        stdout_task = asyncio.create_task(self._drain(process.stdout))
        stderr_task = asyncio.create_task(self._drain(process.stderr))
        try:
            await asyncio.wait_for(process.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            await self._stop(process)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise GitWorktreeError("git_timeout", "Git 操作超时，已停止子进程。") from exc
        except asyncio.CancelledError:
            await self._stop(process)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        if stdout[1] or stderr[1]:
            raise GitWorktreeError("git_output_too_large", "Git 输出超过安全上限。")
        if process.returncode != 0:
            raise GitWorktreeError("git_failed", "Git 操作失败。")
        return stdout[0]

    async def discover(self, cwd: Path) -> RepositoryInfo:
        root_raw = await self._run(cwd, "rev-parse", "--show-toplevel")
        inside = await self._run(cwd, "rev-parse", "--is-inside-work-tree")
        bare = await self._run(cwd, "rev-parse", "--is-bare-repository")
        try:
            root_text = root_raw.decode("utf-8", errors="strict").strip()
            inside_text = inside.decode("ascii", errors="strict").strip()
            bare_text = bare.decode("ascii", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise GitWorktreeError("invalid_git_output", "Git 仓库信息编码无效。") from exc
        root = Path(root_text).resolve(strict=False)
        if not root_text or inside_text != "true" or bare_text != "false" or not root.is_dir():
            raise GitWorktreeError("worktrees_unavailable", "当前目录不是非 bare Git 工作树。")
        return RepositoryInfo(root)

    async def head(self, repo_root: Path) -> str:
        raw = await self._run(repo_root, "rev-parse", "HEAD")
        try:
            return _validate_git_sha(raw.decode("ascii", errors="strict").strip())
        except UnicodeDecodeError as exc:
            raise GitWorktreeError("invalid_git_output", "Git HEAD 编码无效。") from exc

    async def is_dirty(self, repo_root: Path) -> bool:
        raw = await self._run(
            repo_root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        return bool(raw)

    async def branch_exists(self, repo_root: Path, branch: str) -> bool:
        raw = await self._run(repo_root, "branch", "--format=%(refname:short)", "--list", branch)
        try:
            names = raw.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError as exc:
            raise GitWorktreeError("invalid_git_output", "Git 分支列表编码无效。") from exc
        return branch in names

    async def list(self, repo_root: Path) -> tuple[GitWorktreeEntry, ...]:
        return parse_worktree_porcelain(
            await self._run(repo_root, "worktree", "list", "--porcelain", "-z")
        )

    async def add(self, repo_root: Path, path: Path, branch: str, base_commit: str) -> None:
        await self._run(
            repo_root,
            "worktree",
            "add",
            "-b",
            branch,
            str(path),
            base_commit,
        )

    async def status(self, path: Path) -> GitWorktreeState:
        return GitWorktreeState(await self.head(path), await self.is_dirty(path))

    async def remove(self, repo_root: Path, path: Path) -> None:
        await self._run(repo_root, "worktree", "remove", str(path))

    async def delete_branch(self, repo_root: Path, branch: str) -> None:
        await self._run(repo_root, "branch", "-d", branch)
