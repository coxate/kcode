from pathlib import Path

import pytest

from kcode.config import (
    HttpMcpServerConfig,
    MissingMcpEnvironment,
    StdioMcpServerConfig,
    expand_mcp_server,
    load_config,
    mcp_environment_variables,
)


def write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
active_provider: main
providers:
  - {name: main, protocol: openai, model: m, base_url: https://x.test, api_key: k}
"""


def test_mcp_servers_merge_by_name_with_complete_project_override(tmp_path: Path) -> None:
    user = write_config(
        tmp_path / "user.yaml",
        BASE
        + """
mcp_servers:
  shared: {type: stdio, command: old, args: [one]}
  user-only: {type: http, url: https://user.test/mcp}
""",
    )
    project = write_config(
        tmp_path / "project.yaml",
        """
mcp_servers:
  shared: {type: stdio, command: new}
""",
    )

    config = load_config(user, project, {})

    assert [server.name for server in config.mcp_servers] == ["shared", "user-only"]
    shared = config.mcp_servers[0]
    assert isinstance(shared, StdioMcpServerConfig)
    assert shared.command == "new"
    assert shared.args == ()
    assert shared.source == "project"
    assert config.mcp_servers[1].source == "user"


def test_invalid_mcp_section_and_server_are_isolated(tmp_path: Path) -> None:
    bad_section = write_config(tmp_path / "bad-section.yaml", BASE + "mcp_servers: nope\n")
    section_config = load_config(None, bad_section, {})
    assert section_config.mcp_servers == ()
    assert "must be a mapping" in section_config.mcp_warnings[0]

    bad_server = write_config(
        tmp_path / "bad-server.yaml",
        BASE
        + """
mcp_servers:
  broken: {type: stdio}
  working: {type: stdio, command: python}
""",
    )
    server_config = load_config(None, bad_server, {})
    assert [server.name for server in server_config.mcp_servers] == ["working"]
    assert "broken" in server_config.mcp_warnings[0]


@pytest.mark.parametrize(
    "url",
    ["file:///tmp/server", "relative/path", "https://user:pass@example.test/mcp"],
)
def test_http_url_rejects_unsafe_values(tmp_path: Path, url: str) -> None:
    path = write_config(
        tmp_path / "config.yaml",
        BASE + f"mcp_servers:\n  remote: {{type: http, url: '{url}'}}\n",
    )
    config = load_config(None, path, {})
    assert config.mcp_servers == ()
    assert "remote" in config.mcp_warnings[0]


def test_environment_scan_and_expansion_only_touch_env_or_headers() -> None:
    server = HttpMcpServerConfig(
        name="remote",
        source="project",
        type="http",
        url="https://example.test/${NOT_EXPANDED}",
        headers={
            "Authorization": "Bearer ${TOKEN}",
            "X-Combined": "${FIRST}:${SECOND}:${FIRST}",
        },
    )
    assert mcp_environment_variables(server) == ("FIRST", "SECOND", "TOKEN")

    resolved, secrets = expand_mcp_server(
        server,
        {"TOKEN": "top-secret", "FIRST": "one", "SECOND": "two"},
    )
    assert isinstance(resolved, HttpMcpServerConfig)
    assert resolved.url.endswith("/${NOT_EXPANDED}")
    assert resolved.headers == {
        "Authorization": "Bearer top-secret",
        "X-Combined": "one:two:one",
    }
    assert secrets == (
        "one",
        "two",
        "top-secret",
        "Bearer top-secret",
        "one:two:one",
    )


def test_missing_environment_is_server_scoped_and_names_only() -> None:
    server = StdioMcpServerConfig(
        name="local",
        source="project",
        type="stdio",
        command="${COMMAND_IS_LITERAL}",
        env={"TOKEN": "prefix-${MISSING}"},
    )
    with pytest.raises(MissingMcpEnvironment) as caught:
        expand_mcp_server(server, {})
    assert caught.value.variables == ("MISSING",)
    assert "prefix" not in str(caught.value)
