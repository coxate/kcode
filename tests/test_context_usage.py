from kcode.context import (
    UsageEstimator,
    normalize_anthropic_usage,
    normalize_openai_usage,
    resolve_context_window,
)
from kcode.conversation import UserMessage
from kcode.events import TokenUsage


def test_cache_tokens_are_not_added_to_context_input() -> None:
    raw = TokenUsage(
        input_tokens=100,
        output_tokens=10,
        total_tokens=110,
        cache_creation_input_tokens=20,
        cache_read_input_tokens=30,
    )

    anthropic = normalize_anthropic_usage(raw)
    openai = normalize_openai_usage(raw)

    assert anthropic.context_input_tokens == 100
    assert openai.context_input_tokens == 100
    assert anthropic.cache_write_tokens == 20
    assert anthropic.cache_read_tokens == 30


def test_missing_usage_stays_unknown_with_low_confidence() -> None:
    usage = normalize_openai_usage(TokenUsage())

    assert usage.context_input_tokens is None
    assert usage.is_exact is False
    assert usage.confidence == "low"


def test_usage_estimator_anchors_then_estimates_only_increment() -> None:
    estimator = UsageEstimator()
    original = (UserMessage("a" * 35),)
    usage = normalize_openai_usage(TokenUsage(input_tokens=100))
    estimator.record(usage, original)

    estimate = estimator.estimate((*original, UserMessage("b" * 35)))

    assert estimate.estimated_input_tokens == 110
    assert estimate.confidence == "medium"


def test_usage_estimator_discards_anchor_after_model_view_rewrite() -> None:
    estimator = UsageEstimator()
    original = (UserMessage("a" * 3_500),)
    estimator.record(normalize_openai_usage(TokenUsage(input_tokens=5_000)), original)

    estimate = estimator.estimate((UserMessage("summary"),))

    assert estimate.estimated_input_tokens == 2
    assert estimate.confidence == "low"


def test_context_window_resolution_uses_explicit_metadata_then_default() -> None:
    metadata = {"known-model": 96_000}

    assert resolve_context_window(explicit=100_000, model_metadata=metadata) == (
        100_000,
        "high",
    )
    assert resolve_context_window(model="known-model", model_metadata=metadata) == (
        96_000,
        "medium",
    )
    assert resolve_context_window(model="unknown", model_metadata=metadata) == (
        64_000,
        "low",
    )
