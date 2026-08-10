from pathlib import Path

from kcode.skills.catalog import SkillCatalogBuilder


def write_skill(root: Path, directory: str, name: str, description: str, body: str) -> None:
    path = root / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_catalog_precedence_validation_and_limit(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    project = tmp_path / "project"
    write_skill(builtin, "shared", "shared", "builtin", "builtin")
    write_skill(user, "shared", "shared", "user", "user")
    write_skill(project / ".kcode" / "skills", "shared", "shared", "project", "project")
    write_skill(user, "bad-tool", "bad-tool", "bad tool", "body")
    bad_path = user / "bad-tool" / "SKILL.md"
    bad_path.write_text(
        "---\nname: bad-tool\ndescription: bad tool\nallowed_tools: [missing]\n---\nbody\n",
        encoding="utf-8",
    )
    builder = SkillCatalogBuilder(project, builtin_root=builtin, user_root=user)
    catalog = builder.build(
        project_trusted=True,
        tool_names={"read_file"},
        command_names={"help"},
    )
    assert catalog.get("shared").body == "project"
    assert catalog.get("bad-tool") is None
    assert "unknown tools" in "\n".join(catalog.warnings)

    user_only = builder.build(
        project_trusted=False,
        tool_names={"read_file"},
        command_names={"help"},
    )
    assert user_only.get("shared").body == "user"


def test_user_refreshes_body_but_project_uses_trusted_cache(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    project = tmp_path / "project"
    write_skill(user, "live", "live", "live skill", "old user")
    project_root = project / ".kcode" / "skills"
    write_skill(project_root, "fixed", "fixed", "fixed skill", "old project")
    builder = SkillCatalogBuilder(project, builtin_root=builtin, user_root=user)
    catalog = builder.build(project_trusted=True, tool_names=set(), command_names=set())

    write_skill(user, "live", "live", "live skill", "new user")
    assert catalog.load("live").definition.body == "new user"

    write_skill(project_root, "fixed", "fixed", "fixed skill", "new project")
    loaded = catalog.load("fixed")
    assert loaded.definition.body == "old project"
    assert "restart" in " ".join(loaded.warnings).lower()


def test_catalog_prompt_contains_descriptions_not_bodies(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    write_skill(builtin, "one", "one", "short description", "SECRET SOP")
    catalog = SkillCatalogBuilder(
        tmp_path / "project", builtin_root=builtin, user_root=tmp_path / "user"
    ).build(project_trusted=False, tool_names=set(), command_names=set())
    prompt = catalog.available_prompt()
    assert "short description" in prompt
    assert "SECRET SOP" not in prompt


def test_catalog_limit_and_command_conflict_are_deterministic(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    for index in range(31):
        name = f"skill-{index:02d}"
        write_skill(builtin, name, name, f"Description {index}", "body")
    catalog = SkillCatalogBuilder(
        tmp_path / "project", builtin_root=builtin, user_root=tmp_path / "user"
    ).build(project_trusted=False, tool_names=set(), command_names={"skill-00"})
    assert len(catalog) == 29
    assert catalog.get("skill-00") is None
    assert catalog.get("skill-29") is not None
    assert catalog.get("skill-30") is None
    warnings = "\n".join(catalog.warnings)
    assert "command_conflict" in warnings
    assert "catalog_limit" in warnings
