import time

from kcode.memory.models import MemoryRecord, MemoryScope, MemoryStatus, MemoryType
from kcode.memory.prompting import render_prompt


def make_record(index: int, scope: MemoryScope, *, active: bool = True) -> MemoryRecord:
    memory_type = (
        MemoryType.PROJECT_FACT if scope == MemoryScope.PROJECT else MemoryType.USER_PREFERENCE
    )
    return MemoryRecord(
        id=f"mem_{index:032x}",
        type=memory_type,
        scope=scope,
        status=MemoryStatus.ACTIVE if active else MemoryStatus.INACTIVE,
        title=f"Title {index}",
        summary="x" * 300,
        application=f"Apply {index}",
        source_session_id="s",
        source_turn_hash=f"{index:064x}",
        created_at=time.time(),
        updated_at=time.time() + index,
    )


def test_prompt_filters_inactive_and_keeps_both_scopes() -> None:
    user = [make_record(1, MemoryScope.USER), make_record(2, MemoryScope.USER, active=False)]
    project = [make_record(3, MemoryScope.PROJECT)]
    result = render_prompt(user, project)
    assert "Title 1" in result.content
    assert "Title 2" not in result.content
    assert "Title 3" in result.content
    assert "Project memories" in result.content
    assert "User memories" in result.content
    assert result.content.index("Project memories") < result.content.index("User memories")


def test_prompt_budget_is_bounded_without_deleting_records() -> None:
    user = [make_record(index, MemoryScope.USER) for index in range(1, 100)]
    project = [make_record(index, MemoryScope.PROJECT) for index in range(100, 200)]
    result = render_prompt(user, project)
    assert len(result.content.encode("utf-8")) <= 24 * 1024
    assert len(result.content.splitlines()) <= 200
    assert result.excluded > 0
    assert "Project memories" in result.content
    assert "User memories" in result.content
