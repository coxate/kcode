from pathlib import Path

import yaml

from kcode import cli
from kcode.config import load_config


def test_cli_returns_nonzero_before_tui_when_config_is_missing(monkeypatch, capsys, tmp_path: Path) -> None:
    missing_user = tmp_path / "user" / "config.yaml"
    missing_project = tmp_path / "project" / "config.yaml"
    monkeypatch.setattr(cli, "default_config_paths", lambda cwd: (missing_user, missing_project))
    assert cli.main() == 2
    error = capsys.readouterr().err
    assert "configuration error" in error
    assert str(missing_project) in error


def test_example_config_is_valid_yaml_and_contains_no_secret(tmp_path: Path) -> None:
    example = Path(__file__).parents[1] / "config.example.yaml"
    raw = yaml.safe_load(example.read_text(encoding="utf-8"))
    assert raw["active_provider"] == "openai"
    text = example.read_text(encoding="utf-8")
    assert "your-key" not in text
    config = load_config(
        None,
        example,
        {
            "OPENAI_API_KEY": "fake-openai",
            "ANTHROPIC_API_KEY": "fake-anthropic",
            "DEEPSEEK_API_KEY": "fake-deepseek",
        },
    )
    assert set(config.providers) == {"openai", "anthropic", "deepseek"}
