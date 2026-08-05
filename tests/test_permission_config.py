import os
from pathlib import Path

import pytest

from kcode.permissions.config import LocalPermissionStore, PermissionConfigLoader
from kcode.permissions.models import PermissionMode, PermissionPersistenceError


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_permission_config_priority_and_invalid_layer_degradation(tmp_path: Path) -> None:
    user = write(
        tmp_path / "user.yaml",
        "defaultMode: acceptEdits\nallow: [Bash(git *)]\n",
    )
    project = write(tmp_path / "project.yaml", "not: allowed\n")
    local = write(
        tmp_path / "local.yaml",
        "defaultMode: plan\ndeny: [Bash(git push *)]\n",
    )
    settings = PermissionConfigLoader().load(user, project, local)
    assert settings.initial_mode == PermissionMode.PLAN
    assert [layer.name for layer in settings.layers] == ["local", "project", "user"]
    assert settings.layers[1].allow == ()
    assert len(settings.warnings) == 1
    assert "not: allowed" not in settings.warnings[0]


def test_missing_permission_files_are_empty_and_default(tmp_path: Path) -> None:
    settings = PermissionConfigLoader().load(
        tmp_path / "user", tmp_path / "project", tmp_path / "local"
    )
    assert settings.initial_mode == PermissionMode.DEFAULT
    assert settings.warnings == ()
    assert all(not layer.allow and not layer.deny for layer in settings.layers)


def test_local_store_creates_deduplicates_and_preserves_fields(tmp_path: Path) -> None:
    path = tmp_path / ".kcode" / "permissions.local.yaml"
    store = LocalPermissionStore(path)
    first = store.append_allow("Bash(git status)")
    second = store.append_allow("Bash(git status)")
    assert [rule.raw for rule in first.allow] == ["Bash(git status)"]
    assert second == first
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert path.read_text(encoding="utf-8").count("Bash(git status)") == 1


def test_local_store_refuses_to_overwrite_invalid_file(tmp_path: Path) -> None:
    path = write(tmp_path / "permissions.local.yaml", "allow: nope\n")
    before = path.read_bytes()
    with pytest.raises(PermissionPersistenceError):
        LocalPermissionStore(path).append_allow("Bash(git status)")
    assert path.read_bytes() == before


def test_local_store_atomic_replace_failure_preserves_source(monkeypatch, tmp_path: Path) -> None:
    path = write(tmp_path / "permissions.local.yaml", "allow: [Bash(git status)]\n")
    before = path.read_bytes()

    def fail_replace(source, target):
        raise OSError("blocked")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(PermissionPersistenceError):
        LocalPermissionStore(path).append_allow("Bash(git diff)")
    assert path.read_bytes() == before
    assert list(tmp_path.glob(".permissions-*.tmp")) == []


def test_permissions_example_is_valid_project_config(tmp_path: Path) -> None:
    example = Path(__file__).parents[1] / "permissions.example.yaml"
    settings = PermissionConfigLoader().load(tmp_path / "user", example, tmp_path / "local")
    assert settings.warnings == ()
    assert settings.layers[1].allow
