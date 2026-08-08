from kcode.memory.models import CompletedTurn
from kcode.memory.signals import MemorySignalDetector


def turn(user: str, assistant: str = "Done") -> CompletedTurn:
    return CompletedTurn.create("session", user, assistant, "default")


def test_detects_chinese_and_english_durable_signals() -> None:
    detector = MemorySignalDetector()
    assert detector.detect(turn("请记住，这个项目使用 uv")).matched
    assert detector.detect(turn("I prefer concise answers.")).matched
    assert detector.detect(turn("参考文档在 https://example.test/docs")).matched


def test_plain_question_does_not_trigger_model_call() -> None:
    result = MemorySignalDetector().detect(turn("What is a Python tuple?"))
    assert not result.matched
    assert result.kinds == ()
