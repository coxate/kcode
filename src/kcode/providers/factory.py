from __future__ import annotations

from kcode.config import ProviderConfig
from kcode.providers.anthropic import AnthropicProvider
from kcode.providers.base import ChatProvider
from kcode.providers.openai import OpenAIProvider


def create_provider(config: ProviderConfig) -> tuple[ChatProvider, tuple[str, ...]]:
    warnings: list[str] = []
    if config.protocol == "anthropic":
        provider: ChatProvider = AnthropicProvider(config)
    else:
        provider = OpenAIProvider(config)
        if config.thinking:
            warnings.append(
                f"Provider '{config.name}' uses the OpenAI protocol; thinking is ignored."
            )
    return provider, tuple(warnings)
