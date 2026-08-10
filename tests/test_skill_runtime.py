from pathlib import Path

import pytest

from kcode.skills.catalog import SkillCatalog
from kcode.skills.models import SkillDefinition, SkillMeta, SkillSource
from kcode.skills.runtime import SkillRuntime
from kcode.skills.tools import LoadSkillArgs, LoadSkillTool
from kcode.tools.base import ToolContext


def definition(name: str, body: str = "SOP") -> SkillDefinition:
    path = Path("/tmp") / name / "SKILL.md"
    return SkillDefinition(
        SkillMeta(name=name, description=f"{name} description"),
        body,
        SkillSource.BUILTIN,
        path,
        path.parent.parent,
        "digest",
    )


class Session:
    active_skill_names: tuple[str, ...] = ()
    saved: list[tuple[str, ...]]

    def __init__(self, *, succeeds: bool = True) -> None:
        self.saved = []
        self.succeeds = succeeds

    async def record_skill_state(self, names: tuple[str, ...]) -> bool:
        self.saved.append(names)
        return self.succeeds


@pytest.mark.asyncio
async def test_activation_is_ordered_idempotent_and_persisted(tmp_path: Path) -> None:
    runtime = SkillRuntime(SkillCatalog((definition("one"), definition("two"))))
    session = Session()
    runtime.bind_session(session)
    assert (await runtime.activate("one")).ok
    assert (await runtime.activate("two")).ok
    assert (await runtime.activate("one")).ok
    assert runtime.active_names == ("one", "two")
    assert session.saved[-1] == ("one", "two")
    assert "## Active Skills" in runtime.active_prompt()

    tool = LoadSkillTool(runtime)
    result = await tool.execute(LoadSkillArgs(name="one"), ToolContext(tmp_path))
    assert result.status == "success"
    assert "SOP" not in result.to_json()
    assert tool.spec.always_visible


@pytest.mark.asyncio
async def test_activation_failure_does_not_pollute_state() -> None:
    runtime = SkillRuntime(SkillCatalog((definition("one"),)))
    assert (await runtime.activate("one")).ok
    result = await runtime.activate("missing")
    assert not result.ok
    assert runtime.active_names == ("one",)


@pytest.mark.asyncio
async def test_persistence_failure_keeps_memory_state() -> None:
    runtime = SkillRuntime(SkillCatalog((definition("one"),)))
    runtime.bind_session(Session(succeeds=False))
    result = await runtime.activate("one")
    assert result.ok
    assert runtime.active_names == ("one",)
    assert "not saved" in " ".join(result.warnings)


@pytest.mark.asyncio
async def test_active_count_and_body_budget_fail_atomically() -> None:
    definitions = tuple(definition(f"s{index}") for index in range(6))
    runtime = SkillRuntime(SkillCatalog(definitions))
    for index in range(5):
        assert (await runtime.activate(f"s{index}")).ok
    over_count = await runtime.activate("s5")
    assert not over_count.ok
    assert runtime.active_names == tuple(f"s{index}" for index in range(5))

    large = SkillRuntime(
        SkillCatalog((definition("small", "x"), definition("large", "x" * (64 * 1024))))
    )
    assert (await large.activate("small")).ok
    over_size = await large.activate("large")
    assert not over_size.ok
    assert large.active_names == ("small",)
