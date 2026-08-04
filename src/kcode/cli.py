from __future__ import annotations

import sys
from pathlib import Path

from kcode.config import default_config_paths, load_config
from kcode.conversation import Conversation
from kcode.errors import ConfigError
from kcode.providers.factory import create_provider
from kcode.tools.base import ToolContext
from kcode.tools.registry import create_default_registry
from kcode.ui.app import KCodeApp


def main() -> int:
    try:
        user_path, project_path = default_config_paths(Path.cwd())
        config = load_config(user_path, project_path)
        provider, warnings = create_provider(config.active)
    except ConfigError as exc:
        print(f"KCode configuration error: {exc}", file=sys.stderr)
        return 2
    cwd = Path.cwd().resolve()
    registry = create_default_registry()
    context = ToolContext(
        cwd,
        sensitive_values=tuple(
            provider_config.api_key.get_secret_value() for provider_config in config.providers.values()
        ),
    )
    app = KCodeApp(
        provider,
        Conversation(),
        warnings=warnings,
        cwd=cwd,
        registry=registry,
        context=context,
        agent_config=config.agent,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
