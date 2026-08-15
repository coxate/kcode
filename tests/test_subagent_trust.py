from pathlib import Path

import pytest

from kcode.subagents.trust import AgentTrustStore, project_fingerprint


def _agent(project: Path, name: str, body: str) -> Path:
    path = project / ".kcode" / "agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_agent_fingerprint_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    project = tmp_path / "project"
    a = _agent(project, "a", "first")
    b = _agent(project, "b", "second")
    first, warnings = project_fingerprint(project, (b, a))
    second, _ = project_fingerprint(project, (a, b))
    assert warnings == ()
    assert first.fingerprint == second.fingerprint
    b.write_text("changed", encoding="utf-8")
    changed, _ = project_fingerprint(project, (a, b))
    assert changed.fingerprint != first.fingerprint


def test_agent_trust_store_is_private_and_environment_scoped(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    request, _ = project_fingerprint(project, (_agent(project, "a", "first"),))
    store_path = tmp_path / "trust.json"
    store = AgentTrustStore(store_path)
    assert not store.is_trusted(request)
    store.trust(request)
    assert store.is_trusted(request)
    assert store_path.stat().st_mode & 0o777 == 0o600

    override = tmp_path / "isolated" / "trust.json"
    monkeypatch.setenv("KCODE_SUBAGENT_TRUST_PATH", str(override))
    assert AgentTrustStore().path == override


def test_agent_trust_symlink_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "trust.json"
    link.symlink_to(target)
    project = tmp_path / "project"
    request, _ = project_fingerprint(project, (_agent(project, "a", "first"),))
    store = AgentTrustStore(link)
    assert not store.is_trusted(request)
    with pytest.raises(OSError):
        store.trust(request)
