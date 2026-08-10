from pathlib import Path

import pytest

from kcode.skills.trust import SkillTrustStore, project_fingerprint


def project_skill(project: Path, name: str, body: str) -> Path:
    path = project / ".kcode" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_fingerprint_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    project = tmp_path / "project"
    a = project_skill(project, "a", "first")
    b = project_skill(project, "b", "second")
    request1, warnings = project_fingerprint(project, (b, a))
    request2, _ = project_fingerprint(project, (a, b))
    assert warnings == ()
    assert request1.fingerprint == request2.fingerprint
    b.write_text("changed", encoding="utf-8")
    request3, _ = project_fingerprint(project, (a, b))
    assert request3.fingerprint != request1.fingerprint


def test_trust_store_is_project_scoped_and_private(tmp_path: Path) -> None:
    project = tmp_path / "project"
    path = project_skill(project, "a", "first")
    request, _ = project_fingerprint(project, (path,))
    store_path = tmp_path / "config" / "skill-trust.json"
    store = SkillTrustStore(store_path)
    assert not store.is_trusted(request)
    store.trust(request)
    assert store.is_trusted(request)
    assert store_path.stat().st_mode & 0o777 == 0o600


def test_symlink_trust_file_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "trust.json"
    link.symlink_to(target)
    store = SkillTrustStore(link)
    project = tmp_path / "project"
    path = project_skill(project, "a", "first")
    request, _ = project_fingerprint(project, (path,))
    assert not store.is_trusted(request)
    with pytest.raises(OSError):
        store.trust(request)


def test_environment_override_changes_only_skill_trust_path(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "isolated" / "trust.json"
    monkeypatch.setenv("KCODE_SKILL_TRUST_PATH", str(override))
    assert SkillTrustStore().path == override
