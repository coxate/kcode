import asyncio
from pathlib import Path

from kcode.prompting.environment import EnvironmentCollector, EnvironmentSnapshot


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0, *, delay=0.0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.delay = delay
        self.killed = False

    async def communicate(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


def test_environment_snapshot_renders_fixed_order_and_escapes() -> None:
    snapshot = EnvironmentSnapshot(
        "/tmp/a&b",
        "Test <cpu>",
        "2026-08-05",
        "main (clean)",
        "0.3.1",
        "model>x",
    )
    assert (
        snapshot.render()
        == """<environment_context>
Working directory: /tmp/a&amp;b
Platform: Test &lt;cpu&gt;
Date: 2026-08-05
Git: main (clean)
KCode version: 0.3.1
Model: model&gt;x
</environment_context>"""
    )


async def test_git_status_parses_clean_dirty_and_detached(monkeypatch, tmp_path) -> None:
    responses = iter(
        [
            FakeProcess(b"## main...origin/main\n"),
            FakeProcess(b"## feature\n M secret-name.py\n"),
            FakeProcess(b"## HEAD (no branch)\n"),
        ]
    )

    async def create(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    collector = EnvironmentCollector()
    assert await collector._git_status(tmp_path) == "main (clean)"
    assert await collector._git_status(tmp_path) == "feature (dirty)"
    assert await collector._git_status(tmp_path) == "detached (clean)"


async def test_git_status_degrades_for_non_repo_invalid_and_large(monkeypatch, tmp_path) -> None:
    responses = iter(
        [
            FakeProcess(stderr=b"fatal: not a git repository", returncode=128),
            FakeProcess(b"invalid\n"),
            FakeProcess(b"## main\n" + b"x" * (64 * 1024)),
            FakeProcess(b"\xff"),
        ]
    )

    async def create(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    collector = EnvironmentCollector()
    assert await collector._git_status(tmp_path) == "not a repository"
    assert await collector._git_status(tmp_path) == "unavailable"
    assert await collector._git_status(tmp_path) == "unavailable"
    assert await collector._git_status(tmp_path) == "unavailable"


async def test_git_timeout_kills_process(monkeypatch, tmp_path) -> None:
    process = FakeProcess(delay=1)

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    collector = EnvironmentCollector(git_timeout_seconds=0.001)
    assert await collector._git_status(tmp_path) == "unavailable"
    assert process.killed is True


async def test_git_missing_degrades_and_command_is_bounded(monkeypatch, tmp_path) -> None:
    calls = []

    async def create(*args, **kwargs):
        calls.append((args, kwargs))
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    assert await EnvironmentCollector()._git_status(tmp_path) == "unavailable"
    args, kwargs = calls[0]
    assert args == (
        "git",
        "status",
        "--porcelain=v1",
        "--branch",
        "--untracked-files=no",
    )
    assert kwargs["cwd"] == tmp_path


async def test_collect_returns_environment_message(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KCODE_TEST_SECRET", "must-not-appear")

    async def create(*args, **kwargs):
        return FakeProcess(b"## main\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    message = await EnvironmentCollector().collect(
        tmp_path, app_version="0.3.1", model="model-test"
    )
    assert f"Working directory: {tmp_path.resolve()}" in message.content
    assert "Git: main (clean)" in message.content
    assert "KCode version: 0.3.1" in message.content
    assert "Model: model-test" in message.content
    assert "must-not-appear" not in message.content
