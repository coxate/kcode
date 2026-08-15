from __future__ import annotations

from pathlib import Path

from kcode.hooks.catalog import MAX_CONFIG_BYTES, HookCatalogBuilder
from kcode.hooks.models import HookSource


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_catalog_appends_user_then_trusted_project_and_skips_duplicate(tmp_path: Path) -> None:
    user = tmp_path / "user" / "hooks.yaml"
    project = tmp_path / "project"
    project_file = project / ".kcode" / "hooks.yaml"
    write(
        user,
        """hooks:
  - id: shared
    event: startup
    action: {type: prompt, message: user}
""",
    )
    write(
        project_file,
        """hooks:
  - id: shared
    event: startup
    action: {type: prompt, message: project}
  - id: project-only
    event: shutdown
    action: {type: command, command: "true"}
""",
    )
    builder = HookCatalogBuilder(project, user_path=user, project_path=project_file)
    assert [hook.id for hook in builder.build(project_trusted=False).hooks] == ["shared"]
    catalog = builder.build(project_trusted=True)
    assert [hook.id for hook in catalog.hooks] == ["shared", "project-only"]
    assert catalog.hooks[0].source is HookSource.USER
    assert any(warning.code == "duplicate_id" for warning in catalog.warnings)


def test_catalog_rejects_symlink_without_losing_other_layer(tmp_path: Path) -> None:
    project = tmp_path / "project"
    user = tmp_path / "user" / "hooks.yaml"
    target = tmp_path / "target.yaml"
    write(user, "hooks: []\n")
    write(target, "hooks: []\n")
    project_file = project / ".kcode" / "hooks.yaml"
    project_file.parent.mkdir(parents=True)
    project_file.symlink_to(target)
    catalog = HookCatalogBuilder(project, user_path=user, project_path=project_file).build(
        project_trusted=True
    )
    assert catalog.hooks == ()
    assert any(warning.code == "invalid_file" for warning in catalog.warnings)


def test_trust_request_changes_with_project_content(tmp_path: Path) -> None:
    project = tmp_path / "project"
    path = project / ".kcode" / "hooks.yaml"
    write(path, "hooks: []\n")
    builder = HookCatalogBuilder(project, project_path=path, user_path=tmp_path / "missing")
    first, _ = builder.trust_request()
    write(path, "hooks:\n  - id: x\n    event: startup\n    action: {type: prompt, message: x}\n")
    second, _ = builder.trust_request()
    assert first is not None and second is not None
    assert first.fingerprint != second.fingerprint


def test_invalid_yaml_warning_does_not_include_config_body(tmp_path: Path) -> None:
    user = tmp_path / "user" / "hooks.yaml"
    secret = "DO-NOT-LEAK-THIS-CONFIG-BODY"
    write(user, f"hooks:\n  - action: [{secret}\n")
    catalog = HookCatalogBuilder(tmp_path, user_path=user).build(project_trusted=False)
    assert catalog.hooks == ()
    assert all(secret not in warning.render() for warning in catalog.warnings)


def test_catalog_budget_accepts_boundary_and_rejects_one_byte_over(tmp_path: Path) -> None:
    user = tmp_path / "user" / "hooks.yaml"
    user.parent.mkdir(parents=True)
    base = b"hooks: []\n#"
    user.write_bytes(base + b"x" * (MAX_CONFIG_BYTES - len(base)))
    catalog = HookCatalogBuilder(tmp_path, user_path=user).build(project_trusted=False)
    assert catalog.hooks == ()
    assert catalog.warnings == ()

    user.write_bytes(user.read_bytes() + b"x")
    catalog = HookCatalogBuilder(tmp_path, user_path=user).build(project_trusted=False)
    assert any(warning.code == "invalid_file" for warning in catalog.warnings)


def test_catalog_keeps_first_hundred_hooks_in_stable_order(tmp_path: Path) -> None:
    user = tmp_path / "user" / "hooks.yaml"
    definitions = "\n".join(
        f"  - {{id: h{index}, event: startup, action: {{type: prompt, message: x}}}}"
        for index in range(101)
    )
    write(user, f"hooks:\n{definitions}\n")
    catalog = HookCatalogBuilder(tmp_path, user_path=user).build(project_trusted=False)
    assert len(catalog.hooks) == 100
    assert catalog.hooks[0].id == "h0"
    assert catalog.hooks[-1].id == "h99"
    assert any(warning.code == "hook_limit" for warning in catalog.warnings)


def test_binary_and_invalid_utf8_files_fail_safely(tmp_path: Path) -> None:
    user = tmp_path / "user" / "hooks.yaml"
    user.parent.mkdir(parents=True)
    for raw in (b"hooks: []\0private", b"hooks: []\n\xff"):
        user.write_bytes(raw)
        catalog = HookCatalogBuilder(tmp_path, user_path=user).build(project_trusted=False)
        assert catalog.hooks == ()
        assert [warning.code for warning in catalog.warnings] == ["invalid_file"]
