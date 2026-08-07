import json
import os
from pathlib import Path

from kcode.config import HttpMcpServerConfig, StdioMcpServerConfig
from kcode.mcp.trust import McpTrustStore, trust_fingerprint


def stdio(command: str = "python") -> StdioMcpServerConfig:
    return StdioMcpServerConfig(
        name="local",
        source="project",
        type="stdio",
        command=command,
        args=("server.py",),
        env={"TOKEN": "${SECRET_TOKEN}"},
    )


def test_fingerprint_is_stable_and_configuration_bound(tmp_path: Path) -> None:
    first = trust_fingerprint(tmp_path, stdio())
    assert first == trust_fingerprint(tmp_path, stdio())
    assert first != trust_fingerprint(tmp_path, stdio("node"))
    assert first != trust_fingerprint(tmp_path / "other", stdio())

    http = HttpMcpServerConfig(
        name="local",
        source="project",
        type="http",
        url="https://example.test/mcp",
        headers={"Authorization": "Bearer ${SECRET_TOKEN}"},
    )
    assert first != trust_fingerprint(tmp_path, http)


def test_store_is_atomic_private_and_contains_only_hashes(tmp_path: Path) -> None:
    path = tmp_path / ".kcode" / "mcp-trust.json"
    project = tmp_path / "project"
    fingerprint = trust_fingerprint(project, stdio())
    store = McpTrustStore(path)

    assert not store.is_trusted(project, fingerprint)
    store.trust(project, fingerprint)
    assert store.is_trusted(project, fingerprint)
    assert store.clear_project(project)
    assert not store.is_trusted(project, fingerprint)

    raw = path.read_text(encoding="utf-8")
    assert "python" not in raw
    assert "SECRET_TOKEN" not in raw
    assert json.loads(raw)["version"] == 1
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_invalid_store_defaults_to_untrusted_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "mcp-trust.json"
    path.write_text("not-json", encoding="utf-8")
    store = McpTrustStore(path)
    assert not store.is_trusted(tmp_path, "fingerprint")
    assert len(store.warnings) == 1
