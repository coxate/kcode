from kcode.errors import is_prompt_too_long_error


def test_prompt_too_long_error_detection_is_conservative() -> None:
    assert is_prompt_too_long_error(ValueError("context_length_exceeded"))
    assert is_prompt_too_long_error(ValueError("Maximum context length is 64k tokens"))
    assert not is_prompt_too_long_error(ValueError("invalid tool schema"))
