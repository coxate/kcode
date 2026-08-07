from enum import StrEnum


class ProviderErrorKind(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    PROMPT_TOO_LONG = "prompt_too_long"
    INVALID_RESPONSE = "invalid_response"


class ProviderError(Exception):
    def __init__(self, kind: ProviderErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class ConfigError(Exception):
    """A safe, user-facing configuration error."""


_PROMPT_TOO_LONG_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "prompt is too long",
    "prompt too long",
    "request too large",
    "too many tokens",
)


def is_prompt_too_long_error(error: BaseException) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _PROMPT_TOO_LONG_MARKERS)
