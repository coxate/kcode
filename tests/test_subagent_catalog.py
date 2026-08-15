from pathlib import Path

from kcode.subagents.catalog import AgentCatalogBuilder

TOOLS = {
    "read_file",
    "write_file",
    "edit_file",
    "run_command",
    "find_files",
    "search_code",
    "load_skill",
}


def _write(root: Path, name: str, description: str, body: str, extra: str = "") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n",
        encoding="utf-8",
    )


def test_catalog_precedence_prompt_and_validation(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    project = tmp_path / "project"
    _write(plugin_root / "demo" / "agents", "shared", "plugin", "plugin")
    _write(builtin, "shared", "builtin", "builtin")
    _write(user, "shared", "user", "user")
    _write(project / ".kcode" / "agents", "shared", "project", "PROJECT SECRET")
    _write(user, "bad", "bad", "body", "tools: [missing]\n")
    builder = AgentCatalogBuilder(
        project,
        builtin_root=builtin,
        user_root=user,
        plugin_root=plugin_root,
    )
    catalog = builder.build(
        project_trusted=True,
        tool_names=TOOLS,
        provider_names={"main"},
    )
    assert catalog.get("shared").body == "PROJECT SECRET"
    assert catalog.get("bad") is None
    assert "PROJECT SECRET" not in catalog.available_prompt()
    assert "project" in catalog.available_prompt()

    without_project = builder.build(
        project_trusted=False,
        tool_names=TOOLS,
        provider_names={"main"},
    )
    assert without_project.get("shared").body == "user"


def test_invalid_high_priority_definition_falls_back(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    project = tmp_path / "project"
    _write(builtin, "shared", "builtin", "safe body")
    _write(
        project / ".kcode" / "agents",
        "shared",
        "project",
        "invalid body",
        "tools: [missing]\n",
    )
    catalog = AgentCatalogBuilder(
        project,
        builtin_root=builtin,
        user_root=tmp_path / "user",
        plugin_root=tmp_path / "plugins",
    ).build(project_trusted=True, tool_names=TOOLS, provider_names={"main"})
    assert catalog.get("shared").body == "safe body"
    assert "unknown_tool" in " ".join(catalog.warnings)


def test_catalog_detects_changed_project_body(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    project = tmp_path / "project"
    _write(builtin, "base", "base", "body")
    root = project / ".kcode" / "agents"
    _write(root, "fixed", "fixed", "old")
    catalog = AgentCatalogBuilder(project, builtin_root=builtin, user_root=tmp_path / "user").build(
        project_trusted=True,
        tool_names=TOOLS,
        provider_names={"main"},
    )
    _write(root, "fixed", "fixed", "new")
    definition, warnings = catalog.resolve("fixed")
    assert definition.body == "old"
    assert "restart" in " ".join(warnings).lower()


def test_catalog_uses_the_project_bytes_that_were_trusted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    root = project / ".kcode" / "agents"
    _write(root, "fixed", "fixed", "trusted body")
    builder = AgentCatalogBuilder(
        project,
        builtin_root=tmp_path / "builtin",
        user_root=tmp_path / "user",
        plugin_root=tmp_path / "plugins",
    )
    request, _ = builder.trust_request()
    assert request is not None
    _write(root, "fixed", "fixed", "changed after approval")
    catalog = builder.build(
        project_trusted=True,
        tool_names=TOOLS,
        provider_names={"main"},
    )
    assert catalog.get("fixed").body == "trusted body"


def test_packaged_builtin_agents_are_valid(tmp_path: Path) -> None:
    catalog = AgentCatalogBuilder(
        tmp_path,
        user_root=tmp_path / "user",
        plugin_root=tmp_path / "plugins",
    ).build(project_trusted=False, tool_names=TOOLS, provider_names={"main"})
    assert {item.name for item in catalog.summaries()} == {
        "explore",
        "general-purpose",
        "plan",
    }
