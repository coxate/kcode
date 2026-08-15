from __future__ import annotations

from kcode.commands.models import (
    ArgumentPolicy,
    CommandContext,
    CommandSpec,
    CommandType,
)
from kcode.commands.registry import CommandRegistry


async def _help(context: CommandContext) -> None:
    if not context.args:
        lines = ["可用命令："]
        lines.extend(
            f"/{command.name} — {command.description}"
            for command in context.registry.visible_commands()
        )
        await context.host.command_notice("\n".join(lines))
        return
    query = context.args.removeprefix("/").casefold()
    command = context.registry.resolve(query)
    if command is None or command.hidden:
        await context.host.command_notice(
            f"没有名为 `/{context.args.removeprefix('/')}` 的命令。输入 `/help` 查看帮助。",
            "error",
        )
        return
    aliases = ", ".join(f"/{alias}" for alias in command.aliases) or "无"
    await context.host.command_notice(
        "\n".join(
            (
                f"名称：/{command.name}",
                f"别名：{aliases}",
                f"描述：{command.description}",
                f"用法：{command.usage}",
                f"类别：{command.type.value}",
            )
        )
    )


async def _status(context: CommandContext) -> None:
    status = context.host.command_status()
    input_tokens = status.input_tokens if status.input_tokens is not None else "?"
    output_tokens = status.output_tokens if status.output_tokens is not None else "?"
    memories = status.memory_count if status.memory_count is not None else "未启用"
    await context.host.command_notice(
        "\n".join(
            (
                f"模式：{status.mode}",
                f"会话 Token：输入 {input_tokens} / 输出 {output_tokens}",
                f"工具：{status.tool_count}",
                f"记忆：{memories}",
                f"模型：{status.model}",
                f"工作目录：{status.cwd}",
            )
        )
    )


async def _memory(context: CommandContext) -> None:
    inventory = context.host.command_memories()
    if not inventory.enabled:
        await context.host.command_notice("长期记忆未启用。")
        return
    user = ", ".join(f"{item}.md" for item in inventory.user_ids) or "无"
    project = ", ".join(f"{item}.md" for item in inventory.project_ids) or "无"
    await context.host.command_notice(f"user：{user}\nproject：{project}")


async def _permission(context: CommandContext) -> None:
    status = context.host.command_status()
    await context.host.command_notice(
        f"当前权限模式：{status.mode}。使用 Shift+Tab 可切换权限模式。"
    )


async def _session(context: CommandContext) -> None:
    session = context.host.command_session()
    if not session.enabled:
        await context.host.command_notice("当前 App 没有启用会话持久化。")
        return
    await context.host.command_notice(
        f"Session ID：{session.session_id}\nJournal：{session.journal_path}"
    )


async def _exit(context: CommandContext) -> None:
    await context.host.command_exit()


async def _plan(context: CommandContext) -> None:
    context.host.command_enter_plan()
    await context.host.command_notice("已进入 Plan Mode：只允许读取、查找、搜索和白名单只读命令。")
    if context.args:
        await context.host.command_submit_user(context.args)


async def _do(context: CommandContext) -> None:
    has_plan = context.host.command_enter_do()
    suffix = "，下一条请求将使用最新计划一次。" if has_plan else "。"
    await context.host.command_notice("已进入 Do Mode" + suffix)


async def _compact(context: CommandContext) -> None:
    await context.host.command_compact(context.args or None)


async def _resume(context: CommandContext) -> None:
    context.host.command_resume()


async def _clear(context: CommandContext) -> None:
    await context.host.command_clear()


async def _mcp_trust_clear(context: CommandContext) -> None:
    await context.host.command_clear_mcp_trust()


async def _skill(context: CommandContext) -> None:
    skills = context.host.command_skills()
    if not skills:
        await context.host.command_notice("当前没有可用 Skill。")
        return
    await context.host.command_notice(
        "可用 Skills：\n" + "\n".join(f"/{item.name} — {item.description}" for item in skills)
    )


async def _hooks(context: CommandContext) -> None:
    hooks = context.host.command_hooks()
    if not hooks:
        await context.host.command_notice("No hooks loaded.")
        return
    lines = ["Loaded Hooks："]
    for item in hooks:
        flags = []
        if item.once:
            flags.append("once")
        if item.run_async:
            flags.append("async")
        if item.reject:
            flags.append("reject")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"{item.event.value}  {item.id}  {item.action_type}{suffix}  ({item.source.value})"
        )
    await context.host.command_notice("\n".join(lines))


async def _worktree(context: CommandContext) -> None:
    parts = context.args.split()
    usage = "/worktree create|list|status|remove [slug]"
    if not parts:
        await context.host.command_notice(f"用法：{usage}", "error")
        return
    action = parts[0].casefold()
    if action == "list" and len(parts) == 1:
        await context.host.command_worktree_list()
        return
    if action in {"create", "status", "remove"} and len(parts) == 2:
        name = parts[1]
        if action == "create":
            await context.host.command_worktree_create(name)
        elif action == "status":
            await context.host.command_worktree_status(name)
        else:
            await context.host.command_worktree_remove(name)
        return
    await context.host.command_notice(f"用法：{usage}", "error")


async def _team(context: CommandContext) -> None:
    parts = context.args.split()
    usage = "/team status|stop <member>|delete"
    if parts == ["status"]:
        await context.host.command_team_status()
        return
    if parts == ["delete"]:
        await context.host.command_team_delete()
        return
    if len(parts) == 2 and parts[0] == "stop":
        await context.host.command_team_stop(parts[1])
        return
    await context.host.command_notice(f"用法：{usage}", "error")


def register_skill_commands(registry: CommandRegistry, skills) -> None:
    for skill in skills:

        async def execute(context: CommandContext, name: str = skill.name) -> None:
            await context.host.command_execute_skill(name, context.args)

        registry.register(
            CommandSpec(
                skill.name,
                (),
                skill.description,
                f"/{skill.name} [参数]",
                CommandType.PROMPT,
                ArgumentPolicy.OPTIONAL,
                execute,
                "参数",
            )
        )


def create_builtin_registry(*, freeze: bool = True) -> CommandRegistry:
    registry = CommandRegistry()
    definitions = (
        (
            "help",
            ("h", "?"),
            "查看命令帮助",
            "/help [命令]",
            CommandType.LOCAL,
            ArgumentPolicy.OPTIONAL,
            "命令",
            _help,
        ),
        (
            "status",
            ("s",),
            "查看当前运行状态",
            "/status",
            CommandType.LOCAL,
            ArgumentPolicy.NONE,
            None,
            _status,
        ),
        (
            "memory",
            (),
            "查看已加载的长期记忆",
            "/memory",
            CommandType.LOCAL,
            ArgumentPolicy.NONE,
            None,
            _memory,
        ),
        (
            "permission",
            (),
            "查看当前权限模式",
            "/permission",
            CommandType.LOCAL,
            ArgumentPolicy.NONE,
            None,
            _permission,
        ),
        (
            "session",
            (),
            "查看当前会话信息",
            "/session",
            CommandType.LOCAL,
            ArgumentPolicy.NONE,
            None,
            _session,
        ),
        ("exit", (), "退出 KCode", "/exit", CommandType.ACTION, ArgumentPolicy.NONE, None, _exit),
        (
            "plan",
            ("p",),
            "进入计划模式并可提交任务",
            "/plan [任务]",
            CommandType.ACTION,
            ArgumentPolicy.OPTIONAL,
            "任务",
            _plan,
        ),
        ("do", (), "返回执行模式", "/do", CommandType.ACTION, ArgumentPolicy.NONE, None, _do),
        (
            "compact",
            ("c",),
            "压缩当前上下文",
            "/compact [重点]",
            CommandType.ACTION,
            ArgumentPolicy.OPTIONAL,
            "重点",
            _compact,
        ),
        (
            "resume",
            (),
            "恢复一个历史会话",
            "/resume",
            CommandType.ACTION,
            ArgumentPolicy.NONE,
            None,
            _resume,
        ),
        (
            "clear",
            (),
            "清空当前会话",
            "/clear",
            CommandType.ACTION,
            ArgumentPolicy.NONE,
            None,
            _clear,
        ),
        (
            "mcp-trust-clear",
            (),
            "清除当前项目的 MCP 信任",
            "/mcp-trust-clear",
            CommandType.ACTION,
            ArgumentPolicy.NONE,
            None,
            _mcp_trust_clear,
        ),
        (
            "skill",
            (),
            "查看可用 Skill",
            "/skill",
            CommandType.LOCAL,
            ArgumentPolicy.NONE,
            None,
            _skill,
        ),
        (
            "hooks",
            (),
            "查看已加载的 Hook",
            "/hooks",
            CommandType.LOCAL,
            ArgumentPolicy.NONE,
            None,
            _hooks,
        ),
        (
            "worktree",
            (),
            "管理 Git Worktree 隔离目录",
            "/worktree create|list|status|remove [slug]",
            CommandType.ACTION,
            ArgumentPolicy.REQUIRED,
            "子命令 [slug]",
            _worktree,
        ),
        (
            "team",
            (),
            "查看或停止 Agent Team",
            "/team status|stop <member>|delete",
            CommandType.ACTION,
            ArgumentPolicy.REQUIRED,
            "子命令 [member]",
            _team,
        ),
    )
    for name, aliases, description, usage, kind, policy, hint, handler in definitions:
        registry.register(
            CommandSpec(name, aliases, description, usage, kind, policy, handler, hint)
        )
    if freeze:
        registry.freeze()
    return registry
