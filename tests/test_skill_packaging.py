from importlib.resources import files


def test_builtin_skill_resources_are_available() -> None:
    root = files("kcode.skills").joinpath("builtin")
    for name in ("commit", "review", "test"):
        content = root.joinpath(name, "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {name}" in content
        assert "---" in content
