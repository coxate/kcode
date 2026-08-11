from pathlib import Path

import pytest

from kcode.config import SubAgentConfig, load_config
from kcode.errors import ConfigError

BASE = """
active_provider: main
providers:
  - {name: main, protocol: openai, model: m, base_url: https://x.test, api_key: k}
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_subagent_config_defaults_and_user_values(tmp_path: Path) -> None:
    config = load_config(None, _write(tmp_path / "base.yaml", BASE), {})
    assert config.subagents == SubAgentConfig()

    user = _write(
        tmp_path / "user.yaml",
        BASE
        + """
subagents:
  enabled: false
  auto_background_seconds: 3
  max_running: 2
  max_retained: 5
""",
    )
    loaded = load_config(user, None, {})
    assert not loaded.subagents.enabled
    assert loaded.subagents.auto_background_seconds == 3
    assert loaded.subagents.max_running == 2


def test_project_subagent_config_is_ignored(tmp_path: Path) -> None:
    user = _write(tmp_path / "user.yaml", BASE)
    project = _write(tmp_path / "project.yaml", "subagents: {max_running: 16}")
    config = load_config(user, project, {})
    assert config.subagents.max_running == 4
    assert config.subagent_warnings


def test_subagent_config_rejects_retained_below_running(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "bad.yaml",
        BASE + "\nsubagents: {max_running: 4, max_retained: 3}\n",
    )
    with pytest.raises(ConfigError, match="subagents"):
        load_config(path, None, {})
