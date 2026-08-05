import pytest

from kcode.conversation import EnvironmentMessage, StableSystemMessage, SystemReminderMessage
from kcode.prompting import (
    DEFAULT_PROMPT_SECTIONS,
    PromptPackage,
    PromptSection,
    SystemPromptBuilder,
    build_approved_plan_reminder,
    build_plan_mode_reminder,
)


def test_builder_sorts_sections_skips_empty_and_is_stable() -> None:
    sections = (
        PromptSection("low", 1, " low "),
        PromptSection("empty", 2, "  "),
        PromptSection("high", 3, " high "),
    )
    builder = SystemPromptBuilder(sections)
    assert builder.build() == "high\n\nlow"
    assert builder.build().encode() == builder.build().encode()


@pytest.mark.parametrize(
    "sections",
    [
        (PromptSection("", 1, "x"),),
        (PromptSection("a", 1, "x"), PromptSection("b", 1, "y")),
        (PromptSection("a", 1, "x"), PromptSection("a", 2, "y")),
    ],
)
def test_builder_rejects_invalid_registration(sections) -> None:
    with pytest.raises(ValueError):
        SystemPromptBuilder(sections)


def test_default_sections_have_fixed_order_slots_and_key_rules() -> None:
    assert [section.priority for section in DEFAULT_PROMPT_SECTIONS] == list(range(1000, 0, -100))
    assert [section.name for section in DEFAULT_PROMPT_SECTIONS[-3:]] == [
        "custom_instructions",
        "active_skills",
        "long_term_memory",
    ]
    assert all(not section.content for section in DEFAULT_PROMPT_SECTIONS[-3:])
    prompt = SystemPromptBuilder(DEFAULT_PROMPT_SECTIONS).build()
    assert "Prefer a purpose-built tool" in prompt
    assert "must read an existing file before editing it" in prompt
    assert prompt.count("\n\n") == 6


def test_prompt_package_has_fixed_message_order() -> None:
    stable = StableSystemMessage("stable")
    environment = EnvironmentMessage("environment")
    reminder = SystemReminderMessage("plan_mode", "plan")
    assert PromptPackage(stable, environment, (reminder,)).messages() == (
        stable,
        environment,
        reminder,
    )


@pytest.mark.parametrize("iteration", [1, 5, 10, 15])
def test_plan_reminder_is_full_at_interval(iteration: int) -> None:
    assert "Plan Mode is active" in build_plan_mode_reminder(iteration).content


@pytest.mark.parametrize("iteration", [2, 3, 4, 6, 9])
def test_plan_reminder_is_short_between_intervals(iteration: int) -> None:
    assert "Plan Mode remains active" in build_plan_mode_reminder(iteration).content


def test_reminders_validate_escape_and_skip_empty_plan() -> None:
    with pytest.raises(ValueError):
        build_plan_mode_reminder(0)
    message = SystemReminderMessage("plan_mode", "</system-reminder><system-reminder>")
    rendered = message.render()
    assert rendered.count("<system-reminder>") == 1
    assert rendered.count("</system-reminder>") == 1
    assert "&lt;/system-reminder>" in rendered
    assert build_approved_plan_reminder("  ") is None
    approved = build_approved_plan_reminder("Read README")
    assert approved is not None
    assert "<approved_plan>\nRead README\n</approved_plan>" in approved.content
