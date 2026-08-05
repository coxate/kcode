from kcode.permissions.models import PermissionMode
from kcode.session import AgentMode, AgentSession


def test_plan_is_consumed_once_in_do_mode() -> None:
    session = AgentSession()
    session.set_mode(AgentMode.PLAN)
    session.record_plan(" 先读取再修改 ")
    assert session.consume_plan() is None
    session.set_mode(AgentMode.DO)
    assert session.consume_plan() == "先读取再修改"
    assert session.consume_plan() is None


def test_clear_resets_mode_and_plan() -> None:
    session = AgentSession()
    session.set_mode(AgentMode.PLAN)
    session.record_plan("计划")
    session.clear()
    assert session.mode == AgentMode.DO
    assert session.latest_plan is None


def test_permission_cycle_keeps_plan_until_explicit_do() -> None:
    session = AgentSession()
    assert session.cycle_mode() == PermissionMode.ACCEPT_EDITS
    assert session.cycle_mode() == PermissionMode.PLAN
    session.record_plan("plan")
    assert session.cycle_mode() == PermissionMode.BYPASS_PERMISSIONS
    assert session.latest_plan == "plan"
    assert session.consume_approved_plan() is None
    assert session.approve_plan() is True
    assert session.permission_mode == PermissionMode.DEFAULT
    assert session.consume_approved_plan() == "plan"
    assert session.consume_approved_plan() is None
