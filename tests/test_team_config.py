from pathlib import Path

import pytest

from kcode.config import TeamConfig, load_config
from kcode.errors import ConfigError

BASE = """
active_provider: main
providers:
  - {name: main, protocol: openai, model: m, base_url: https://x.test, api_key: k}
"""


def write(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def test_team_defaults_and_limits() -> None:
    assert TeamConfig().enabled is False
    assert TeamConfig().max_members == 3
    for value in (1, 2, 3):
        assert TeamConfig(max_members=value).max_members == value
    with pytest.raises(ValueError):
        TeamConfig(max_members=0)
    with pytest.raises(ValueError):
        TeamConfig(max_members=4)


def test_only_user_config_can_enable_teams(tmp_path: Path) -> None:
    user = write(tmp_path / "user.yaml", BASE + "\nteams: {enabled: false, max_members: 2}\n")
    project = write(tmp_path / "project.yaml", "teams: {enabled: true, max_members: 3}\n")
    config = load_config(user, project, {})
    assert config.teams == TeamConfig(enabled=False, max_members=2)
    assert config.team_warnings

    user.write_text(BASE + "\nteams: {enabled: true, max_members: 1}\n", encoding="utf-8")
    config = load_config(user, project, {})
    assert config.teams == TeamConfig(enabled=True, max_members=1)


def test_invalid_team_config_is_actionable(tmp_path: Path) -> None:
    path = write(tmp_path / "config.yaml", BASE + "\nteams: {max_members: 4}\n")
    with pytest.raises(ConfigError, match="max_members"):
        load_config(path, None, {})

    path.write_text(BASE + "\nteams: nope\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="teams"):
        load_config(path, None, {})
