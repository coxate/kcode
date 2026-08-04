import asyncio
from dataclasses import dataclass

from kcode.session import AgentMode
from kcode.tools.base import ToolCall, ToolContext, ToolEffect, ToolResult
from kcode.tools.executor import ToolExecutor
from kcode.tools.policy import ToolPolicy
from kcode.tools.registry import ToolRegistry
from kcode.tools.scheduler import ToolScheduler


async def allow(_request):
    return True


@dataclass
class Probe:
    active: int = 0
    peak: int = 0
    side_effect_active: bool = False
    overlap_with_side_effect: bool = False


def make_tool(name, effect, probe: Probe):
    from kcode.tools.base import ToolArguments, ToolSpec

    class Args(ToolArguments):
        pass

    class ProbeTool:
        spec = ToolSpec(name, name, Args, effect)

        async def execute(self, arguments, context):
            if effect == ToolEffect.SIDE_EFFECT:
                probe.side_effect_active = True
            elif probe.side_effect_active:
                probe.overlap_with_side_effect = True
            probe.active += 1
            probe.peak = max(probe.peak, probe.active)
            await asyncio.sleep(0.03)
            probe.active -= 1
            if effect == ToolEffect.SIDE_EFFECT:
                probe.side_effect_active = False
            return ToolResult.success({"name": name})

    return ProbeTool()


async def test_scheduler_parallelizes_readers_and_serializes_side_effects(tmp_path) -> None:
    probe = Probe()
    registry = ToolRegistry()
    for name, effect in (
        ("read_a", ToolEffect.READ_ONLY),
        ("read_b", ToolEffect.READ_ONLY),
        ("write", ToolEffect.SIDE_EFFECT),
        ("read_c", ToolEffect.READ_ONLY),
    ):
        registry.register(make_tool(name, effect, probe))
    executor = ToolExecutor(registry, ToolPolicy(tmp_path))
    prepared = tuple(
        executor.prepare(ToolCall(index, str(index), name, "{}"), ToolContext(tmp_path), AgentMode.DO)
        for index, name in enumerate(("read_a", "read_b", "write", "read_c"))
    )
    scheduler = ToolScheduler(executor, max_parallel=2)
    results = await scheduler.execute(prepared, ToolContext(tmp_path), allow)

    assert probe.peak == 2
    assert probe.overlap_with_side_effect is False
    assert [result.data["name"] for result in results] == ["read_a", "read_b", "write", "read_c"]
