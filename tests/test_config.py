from pathlib import Path

import pytest

from kcode.config import load_config
from kcode.errors import ConfigError


def write_config(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_project_config_field_merges_user_provider(tmp_path: Path) -> None:
    user = write_config(
        tmp_path / "user.yaml",
        """
active_provider: main
providers:
  - name: main
    protocol: openai
    model: old-model
    base_url: https://example.test/v1
    api_key: ${TEST_KEY}
    thinking: false
""",
    )
    project = write_config(
        tmp_path / "project.yaml",
        """
active_provider: main
providers:
  - name: main
    model: new-model
""",
    )
    config = load_config(user, project, {"TEST_KEY": "secret-value"})
    assert config.active.model == "new-model"
    assert config.active.protocol == "openai"
    assert config.active.base_url == "https://example.test/v1"
    assert config.active.api_key.get_secret_value() == "secret-value"
    assert "secret-value" not in repr(config)


def test_project_can_add_and_activate_provider(tmp_path: Path) -> None:
    user = write_config(
        tmp_path / "user.yaml",
        """
active_provider: first
providers:
  - {name: first, protocol: openai, model: one, base_url: https://one.test, api_key: one}
""",
    )
    project = write_config(
        tmp_path / "project.yaml",
        """
active_provider: second
providers:
  - {name: second, protocol: anthropic, model: two, base_url: https://two.test, api_key: two}
""",
    )
    config = load_config(user, project, {})
    assert set(config.providers) == {"first", "second"}
    assert config.active.name == "second"


def test_provider_context_window_is_optional_and_positive(tmp_path: Path) -> None:
    configured = write_config(
        tmp_path / "configured.yaml",
        """
active_provider: main
providers:
  - name: main
    protocol: openai
    model: m
    base_url: https://x.test
    api_key: k
    context_window: 100000
""",
    )
    config = load_config(None, configured, {})
    assert config.active.context_window == 100000

    invalid = write_config(
        tmp_path / "invalid.yaml",
        """
active_provider: main
providers:
  - name: main
    protocol: openai
    model: m
    base_url: https://x.test
    api_key: k
    context_window: 0
""",
    )
    with pytest.raises(ConfigError, match="context_window"):
        load_config(None, invalid, {})


def test_agent_config_defaults_and_field_merge(tmp_path: Path) -> None:
    user = write_config(
        tmp_path / "user.yaml",
        """
active_provider: main
agent: {max_iterations: 12, max_parallel_tools: 3}
providers:
  - {name: main, protocol: openai, model: m, base_url: https://x.test, api_key: k}
""",
    )
    project = write_config(tmp_path / "project.yaml", "agent: {max_parallel_tools: 6}")
    config = load_config(user, project, {})
    assert config.agent.max_iterations == 12
    assert config.agent.max_parallel_tools == 6


@pytest.mark.parametrize(
    "agent",
    ("{max_iterations: 0}", "{max_iterations: 101}", "{max_parallel_tools: 17}"),
)
def test_agent_config_rejects_invalid_limits(tmp_path: Path, agent: str) -> None:
    path = write_config(
        tmp_path / "bad-agent.yaml",
        f"""
active_provider: main
agent: {agent}
providers:
  - {{name: main, protocol: openai, model: m, base_url: https://x.test, api_key: k}}
""",
    )
    with pytest.raises(ConfigError, match="agent"):
        load_config(None, path, {})


def test_missing_environment_key_is_safe_and_actionable(tmp_path: Path) -> None:
    config = write_config(
        tmp_path / "config.yaml",
        """
active_provider: main
providers:
  - {name: main, protocol: openai, model: m, base_url: https://x.test, api_key: "${MISSING_SECRET}"}
""",
    )
    with pytest.raises(ConfigError) as caught:
        load_config(None, config, {})
    message = str(caught.value)
    assert str(config) in message
    assert "api_key" in message
    assert "MISSING_SECRET" in message
    assert "Set it" in message


@pytest.mark.parametrize(
    "body, field",
    [
        ("providers: nope", "providers"),
        ("active_provider: absent\nproviders: []", "active_provider"),
        (
            "active_provider: x\nproviders:\n"
            "  - {name: x, protocol: bad, model: m, base_url: u, api_key: k}",
            "protocol",
        ),
    ],
)
def test_invalid_config_names_field(tmp_path: Path, body: str, field: str) -> None:
    path = write_config(tmp_path / "bad.yaml", body)
    with pytest.raises(ConfigError, match=field):
        load_config(None, path, {})
