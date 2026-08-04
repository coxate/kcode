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
    session = AgentSession(AgentMode.PLAN, "计划")
    session.clear()
    assert session.mode == AgentMode.DO
    assert session.latest_plan is None
