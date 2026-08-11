from __future__ import annotations

from kcode.config import ProviderConfig
from kcode.providers.base import ChatProvider
from kcode.providers.factory import create_provider


class ProviderPool:
    def __init__(self, active: ChatProvider, configs: dict[str, ProviderConfig]) -> None:
        self._configs = dict(configs)
        active_config = getattr(active, "config", None)
        active_name = getattr(active_config, "name", None)
        self._providers: dict[str, ChatProvider] = (
            {active_name: active} if isinstance(active_name, str) else {}
        )
        self.warnings: list[str] = []

    @property
    def names(self) -> set[str]:
        """Return configured provider names without exposing mutable internals."""
        return set(self._configs)

    def get(self, name: str, parent: ChatProvider) -> ChatProvider:
        if name == "inherit":
            return parent
        if name in self._providers:
            return self._providers[name]
        config = self._configs.get(name)
        if config is None:
            raise KeyError(f"Unknown Provider: {name}")
        provider, warnings = create_provider(config)
        self._providers[name] = provider
        self.warnings.extend(warnings)
        return provider
