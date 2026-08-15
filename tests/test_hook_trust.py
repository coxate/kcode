from __future__ import annotations

from pathlib import Path

from kcode.hooks.catalog import HookTrustRequest
from kcode.hooks.trust import HookTrustStore


def request(root: Path, fingerprint: str = "abc") -> HookTrustRequest:
    return HookTrustRequest(root, root / ".kcode" / "hooks.yaml", fingerprint, ("one",))


def test_hook_trust_is_independent_and_content_sensitive(tmp_path: Path) -> None:
    store = HookTrustStore(tmp_path / "hook-trust.json")
    first = request(tmp_path / "project")
    assert not store.is_trusted(first)
    store.trust(first)
    assert store.is_trusted(first)
    assert not store.is_trusted(request(first.project_root, "changed"))


def test_hook_trust_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text('{"version":1,"projects":{}}', encoding="utf-8")
    path = tmp_path / "hook-trust.json"
    path.symlink_to(target)
    store = HookTrustStore(path)
    assert not store.is_trusted(request(tmp_path / "project"))


def test_hook_trust_path_override_does_not_require_changing_home(
    tmp_path: Path, monkeypatch
) -> None:
    override = tmp_path / "isolated" / "hook-trust.json"
    monkeypatch.setenv("KCODE_HOOK_TRUST_PATH", str(override))
    store = HookTrustStore()
    assert store.path == override
    store.trust(request(tmp_path / "project"))
    assert override.exists()
