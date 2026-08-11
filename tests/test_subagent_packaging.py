from importlib.resources import files


def test_builtin_agent_resources_are_available() -> None:
    root = files("kcode.subagents").joinpath("builtin")
    for name in ("general-purpose", "explore", "plan"):
        content = root.joinpath(f"{name}.md").read_text(encoding="utf-8")
        assert f"name: {name}" in content
        assert "---" in content
