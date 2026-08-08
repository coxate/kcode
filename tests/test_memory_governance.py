import json
import time

from kcode.events import StreamCompleted, TextDelta
from kcode.memory.governance import MemoryGovernor
from kcode.memory.models import (
    GOVERNANCE_INTERVAL_SECONDS,
    GOVERNANCE_MIN_RECORDS,
    MemoryRecord,
    MemoryScope,
    MemoryState,
    MemoryType,
)


class Provider:
    display_name = "fake"
    model_name = "fake"

    def __init__(self, response: str = "") -> None:
        self.response = response

    async def stream(self, messages, tools=(), tool_choice="auto"):
        yield TextDelta(self.response)
        yield StreamCompleted()


def records(count: int) -> tuple[MemoryRecord, ...]:
    now = time.time()
    return tuple(
        MemoryRecord(
            id=f"mem_{index:032x}",
            type=MemoryType.PROJECT_FACT,
            scope=MemoryScope.PROJECT,
            title=f"Fact {index}",
            summary="Summary",
            application="Apply",
            source_session_id="s",
            source_turn_hash=f"{index:064x}",
            created_at=now,
            updated_at=now,
        )
        for index in range(count)
    )


def test_governance_requires_all_three_thresholds() -> None:
    governor = MemoryGovernor(Provider())
    now = time.time()
    sessions = tuple(f"s-{index}" for index in range(5))
    assert governor.due(
        records(GOVERNANCE_MIN_RECORDS),
        MemoryState(completed_session_ids=sessions),
        now,
    )
    assert not governor.due(records(9), MemoryState(completed_session_ids=sessions), now)
    assert not governor.due(records(10), MemoryState(completed_session_ids=sessions[:4]), now)
    assert not governor.due(
        records(10),
        MemoryState(
            last_governed_at=now - GOVERNANCE_INTERVAL_SECONDS + 1,
            completed_session_ids=sessions,
        ),
        now,
    )


async def test_governance_can_only_propose_reviewable_non_delete_actions() -> None:
    source = records(2)
    response = json.dumps(
        {
            "candidates": [
                {
                    "action": "merge",
                    "target_ids": [source[0].id, source[1].id],
                    "title": "Merged fact",
                    "summary": "One canonical fact.",
                    "application": "Use the canonical version.",
                    "reason": "Duplicates",
                    "evidence": "Same meaning",
                }
            ]
        }
    )
    proposals = await MemoryGovernor(Provider(response)).propose(source)
    assert len(proposals) == 1
    assert proposals[0].action.value == "merge"
    assert proposals[0].target_ids == (source[0].id, source[1].id)
