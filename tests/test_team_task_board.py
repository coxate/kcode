import pytest

from kcode.teams import TaskBoard, TeamError, TeamTaskStatus


def create(board: TaskBoard, title: str, blocked_by=()):
    return board.create(
        title=title,
        description=f"do {title}",
        assignee="alice",
        blocked_by=blocked_by,
        created_by="lead",
        valid_assignees=("alice", "bob"),
    )


def test_task_board_dependency_and_ready_state() -> None:
    board = TaskBoard()
    first = create(board, "first")
    second = create(board, "second", (first.id,))
    assert board.ready(first)
    assert not board.ready(second)
    with pytest.raises(TeamError, match="not completed"):
        board.update(
            second.id,
            status=TeamTaskStatus.IN_PROGRESS,
            valid_assignees=("alice", "bob"),
        )
    board.update(
        first.id,
        status=TeamTaskStatus.COMPLETED,
        valid_assignees=("alice", "bob"),
    )
    assert board.ready(second)


def test_task_board_rejects_cycles_atomically() -> None:
    board = TaskBoard()
    first = create(board, "first")
    second = create(board, "second", (first.id,))
    before = board.tasks[first.id]
    with pytest.raises(TeamError, match="acyclic"):
        board.update(
            first.id,
            assignee="bob",
            add_blocked_by=(second.id,),
            valid_assignees=("alice", "bob"),
        )
    assert board.tasks[first.id] == before


def test_terminal_task_is_immutable_and_cancelled_dependency_blocks() -> None:
    board = TaskBoard()
    first = create(board, "first")
    second = create(board, "second", (first.id,))
    board.update(
        first.id,
        status=TeamTaskStatus.CANCELLED,
        valid_assignees=("alice", "bob"),
    )
    with pytest.raises(TeamError, match="immutable"):
        board.update(
            first.id,
            status=TeamTaskStatus.PENDING,
            valid_assignees=("alice", "bob"),
        )
    with pytest.raises(TeamError, match="not completed"):
        board.update(
            second.id,
            status=TeamTaskStatus.IN_PROGRESS,
            valid_assignees=("alice", "bob"),
        )


def test_inactive_member_cannot_receive_new_in_progress_task() -> None:
    board = TaskBoard()
    task = create(board, "first")
    with pytest.raises(TeamError, match="stopped or failed"):
        board.update(
            task.id,
            status=TeamTaskStatus.IN_PROGRESS,
            assignee="alice",
            valid_assignees=("alice", "bob"),
            inactive_assignees=("alice",),
        )
