from __future__ import annotations

import sys
from pathlib import Path

from kcode.config import default_config_paths, load_config
from kcode.conversation import Conversation
from kcode.errors import ConfigError
from kcode.history.runtime import SessionCoordinator
from kcode.instructions import InstructionLoader
from kcode.mcp import McpManager, McpTrustStore
from kcode.memory.runtime import MemoryCoordinator
from kcode.permissions import (
    LocalPermissionStore,
    PermissionConfigLoader,
    default_permission_paths,
)
from kcode.prompting import DEFAULT_PROMPT_SECTIONS, SystemPromptBuilder
from kcode.providers.factory import create_provider
from kcode.tools.base import ToolContext
from kcode.tools.registry import create_default_registry
from kcode.ui.app import KCodeApp


def main() -> int:
    try:
        user_path, project_path = default_config_paths(Path.cwd())
        config = load_config(user_path, project_path)
        permission_paths = default_permission_paths(Path.cwd())
        permission_settings = PermissionConfigLoader().load(*permission_paths)
        provider, warnings = create_provider(config.active)
    except ConfigError as exc:
        print(f"KCode configuration error: {exc}", file=sys.stderr)
        return 2
    cwd = Path.cwd().resolve()
    registry = create_default_registry()
    mcp_manager = (
        McpManager(config.mcp_servers, cwd, McpTrustStore()) if config.mcp_servers else None
    )
    context = ToolContext(
        cwd,
        sensitive_values=tuple(
            provider_config.api_key.get_secret_value()
            for provider_config in config.providers.values()
        ),
    )
    instruction_bundle = InstructionLoader().load(cwd)
    instruction_warnings = tuple(
        f"KCODE.md warning [{warning.code}] {warning.path}: {warning.detail}"
        for warning in instruction_bundle.warnings
    )
    prompt_builder = SystemPromptBuilder(DEFAULT_PROMPT_SECTIONS)
    if instruction_bundle.content:
        prompt_builder = prompt_builder.with_content(
            "custom_instructions",
            instruction_bundle.content,
        )
    memory_coordinator = None
    memory_warnings: tuple[str, ...] = config.memory_warnings
    if config.memory.enabled:
        try:
            memory_coordinator = MemoryCoordinator(
                cwd,
                provider,
                sensitive_values=context.sensitive_values,
            )
            memory_prompt = memory_coordinator.start()
            memory_warnings = (
                *memory_warnings,
                *memory_coordinator.warnings,
                *memory_prompt.warnings,
            )
            prompt_builder = prompt_builder.with_content(
                "long_term_memory",
                memory_prompt.content,
            )
        except Exception as exc:
            memory_warnings = (
                *memory_warnings,
                f"Long-term memory was disabled after initialization failed: {exc}",
            )
            memory_coordinator = None
    coordinator = SessionCoordinator(
        cwd,
        provider,
        sensitive_values=context.sensitive_values,
        conversation=Conversation(),
        close_listeners=(memory_coordinator,) if memory_coordinator is not None else (),
    )
    app = KCodeApp(
        provider,
        coordinator.current.conversation,
        warnings=(
            *warnings,
            *permission_settings.warnings,
            *config.mcp_warnings,
            *instruction_warnings,
            *memory_warnings,
        ),
        cwd=cwd,
        registry=registry,
        context=context,
        agent_config=config.agent,
        permission_settings=permission_settings,
        permission_store=LocalPermissionStore(permission_paths[2]),
        mcp_manager=mcp_manager,
        prompt_builder=prompt_builder,
        coordinator=coordinator,
        memory_coordinator=memory_coordinator,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
