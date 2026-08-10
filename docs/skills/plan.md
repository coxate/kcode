# Kcode Skill MVP + Fork 技术实施 Plan

## Summary

新增独立的 `kcode.skills` 领域包，负责 Skill 的解析、Catalog、项目信任、Active 状态及 inline/fork 执行。现有 UI、AgentRunner、Command、Tool 和 Session 系统只增加必要接缝。

启动顺序统一为：Skill 发现与信任 → MCP 注册 → 最终校验 → Prompt/命令更新 → Registry 冻结 → 启用输入。

## 核心类型与接口

- `SkillMeta`：`name`、`description`、`allowed_tools`、`mode`、`fork_context`。
- `SkillDefinition`：元数据、正文缓存、来源、受控路径、启动期摘要。
- `SkillCatalog`：最终有效 Skill 映射，提供查找、稳定 Prompt、调用前安全刷新。
- `SkillTrustStore`：对“规范化项目路径 + 排序后的相对路径和原始字节”计算 SHA-256，原子写入独立信任文件。
- `SkillRuntime`：保存唯一的 Active Skill 正文状态，提供激活、恢复、动态 Prompt 和 session 快照回调。
- `SkillExecutor`：渲染参数并分派 inline/fork；fork 负责隔离运行、事件转发和主会话回流。
- `LoadSkillTool`：只读且 `always_visible`；结果只返回名称、Active 列表和 warning，不返回正文。
- `ToolSpec.always_visible`：默认 `False`。
- `ToolRegistry.restricted_view(names)`：共享工具对象的受限视图，自动保留 always-visible 工具。
- `CommandHost.command_submit_user(model_text, display_text=None)`：分离模型文本与 UI 标签。
- `CommandHost.command_execute_skill(name, args)`：Skill 命令统一入口。
- `SessionRuntime.active_skill_names`：仅保存有序名称。
- `SkillStateRecord`：JSONL 记录 `{type, ts, names}`，不改变现有消息结构和 schema 版本。

## Implementation Changes

### 解析、Catalog 与信任

- 使用 PyYAML `safe_load` 和严格 Pydantic 模型；先检查路径、文件类型、符号链接、字节大小、UTF-8 和二进制，再解析 frontmatter。
- 按内置、用户、项目顺序覆盖；最终按名称排序，超过 30 项时保留前 30 项并产生安全 warning。
- 项目 Skill 在解析和进入 Prompt 前确认指纹；拒绝或信任文件异常时排除全部项目 Skill。
- 用户 Skill 调用时允许刷新正文，但元数据必须与启动期一致；否则继续使用缓存并提示重启。
- 项目 Skill 启动后变化时只使用已信任缓存，不能执行新内容。

### 启动、命令与 Prompt

- `create_builtin_registry()` 增加兼容的延迟冻结选项；默认行为保持不变，Kcode 启动明确使用未冻结 Registry。
- 删除硬编码 `/review`，增加 `/skill`，并为最终 Catalog 注册动态命令；默认三个内置 Skill 时 `/help` 为 16 条。
- 输入框在有无 MCP 两条路径下都先禁用；最终校验、Prompt 更新和命令冻结完成后才启用。
- 稳定 System Prompt 新增 `Available Skills` 名称、描述和 `load_skill` 说明。
- AgentRunner 每次模型迭代重新拼接 Active Skills 动态环境，保证工具激活后的下一次迭代立即看到 SOP。
- 显式 `/<skill>` 只执行一次；只有模型调用 `load_skill` 才产生持续激活状态。

### Inline、Fork 与权限

- inline 将渲染后的 Skill Prompt 交给主 AgentRunner；`allowed_tools` 仅作为建议和启动校验。
- fork 创建独立 Conversation、ContextManager 和 SkillRuntime，并复用当前 Provider、模型、权限模式、审批回调、工具上下文和迭代配置。
- fork 工具集合为：Skill 白名单（空表示全部）与当前模式可见工具的交集，再加入 always-visible 工具；执行器绑定同一个受限 Registry。
- `recent` 只复制最近两个没有工具调用或 tool_result 的完整文本轮次；`none` 不复制历史。
- 子 `load_skill` 只能在父白名单内提供 SOP，不能扩大 Registry，也不能改变主 Active 状态。
- 成功与非取消失败都规范化为“渲染后的 user Prompt + assistant 结果”写回主会话；取消不回流。
- 子 Token 事件进入 UI 请求和 session 累计；子 ContextManager 独立记账，不改变主上下文锚。
- AgentRunner 增加外部完整轮次提交接口，统一处理 Conversation、Journal 和长期记忆候选。

### 状态、历史与恢复

- 每次成功激活后追加完整 Active 名称快照；写入失败不回滚内存状态，并返回降级 warning。
- SessionStore 读取最后一个 `skill_state`；未知记录仍可被旧版本安全跳过。
- `/clear` 绑定空 SkillRuntime。
- `/resume` 使用当前已信任 Catalog 恢复正文；缺失、失效或未信任项逐项跳过并提示。
- 无快照的旧会话按空 Active 状态恢复，无需迁移。

## 模块交互

### 启动

1. CLI 创建未冻结的内置 Command Registry、本地 Tool Registry、空 Skill Runtime 和 `load_skill`。
2. TUI 挂载后保持输入禁用，发现三级 Skill；项目候选只用于生成指纹，批准后才进入解析和 Catalog。
3. MCP 完成信任与连接，将工具注册进 Tool Registry。
4. Catalog 使用最终工具名和内置命令/别名做统一校验，生成有效定义和 warning。
5. Runner 更新稳定 Catalog Prompt，命令层注册 `/skill` 和动态 Skill，随后冻结 Registry 并启用输入。

### 激活

1. 模型调用 `load_skill(name)`。
2. 工具委托 Skill Runtime 从 Catalog 安全获取正文并执行预算检查。
3. Runtime 原子更新内存 Active 状态，然后向当前 Session Runtime 追加完整名称快照。
4. 工具只返回激活结果；Runner 下一次迭代把最新 Active Skills 拼入动态环境。

### 显式执行

1. Command Dispatcher 将 `/<name> [args]` 交给 `CommandHost.command_execute_skill`。
2. Skill Executor 获取调用时有效正文并渲染 `$ARGUMENTS`。
3. inline 通过主 Runner 正常执行；fork 通过隔离 Runner 执行。
4. UI 显示简短命令标签，模型和 Journal 使用完整渲染文本。

### Fork 回流

1. 子 Runner 使用受限 Tool Registry、独立 Conversation、ContextManager 和 Skill Runtime。
2. Token 与进度事件转发给主 UI；子上下文用量不写入主 ContextManager。
3. 成功或非取消失败通过主 Runner 的外部轮次提交接口写入 Conversation、Journal 和长期记忆候选。
4. Ctrl+C 取消子 Runner，不向主历史追加消息。

### Session 切换

1. `/clear` 创建新 Session Runtime 并将 Skill Runtime 绑定为空名称列表。
2. `/resume` 从最后一个 `skill_state` 取得名称，并从当前 Catalog 逐项重载。
3. 无效项产生 warning；有效项按原顺序恢复，不重新写一条状态快照。

## 文件组织

```text
src/kcode/skills/
├── __init__.py
├── models.py
├── parser.py
├── catalog.py
├── trust.py
├── runtime.py
├── executor.py
├── tools.py
└── builtin/
    ├── commit/SKILL.md
    ├── review/SKILL.md
    └── test/SKILL.md
```

现有 `commands`、`history`、`prompting`、`orchestration`、`tools`、`ui/app.py` 和 `cli.py` 增加集成接缝；新增项目 Skill 信任确认界面。构建配置验证三个内置资源进入 wheel。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| YAML 解析 | `yaml.safe_load` + Pydantic `extra="forbid"` | 复用现有依赖并严格拒绝未知字段 |
| Catalog 超限 | 最终覆盖后按名称排序，保留前 30 项 | 结果稳定、可测试，不依赖文件系统遍历顺序 |
| 信任粒度 | 项目路径、相对路径和原始字节的 SHA-256 | 目录顺序变化不误报，真实内容变化必然重新确认 |
| 信任写入 | 独立文件、原子替换、用户私有权限 | 不混用 MCP 信任，避免半写入与越权读取 |
| E2E 信任隔离 | `KCODE_SKILL_TRUST_PATH` 可覆盖信任文件 | 默认路径不变，测试无需修改 `HOME` 或真实用户信任 |
| 用户正文刷新 | 元数据不变时采用新正文，否则缓存并提示重启 | 支持安全更新正文而不热注册命令或权限 |
| 显式执行与激活 | Slash Command 一次性执行；仅 `load_skill` 持续激活 | 避免一次命令意外改变后续所有对话 |
| Fork 工具边界 | 可见定义和实际执行共用受限 Registry | 防止只隐藏工具描述却仍可实际调用 |
| Fork 失败历史 | 非取消失败规范化为成对 user/assistant | 保持 Conversation 和 JSONL 可恢复结构 |
| Skill 状态日志 | schema 1 中新增独立记录类型 | 不修改旧消息协议，无需迁移 |
| 内置资源 | `SKILL.md` 随 Python wheel 分发 | 安装后不依赖源码仓库路径 |

## Test Plan

- 解析：合法和非法 frontmatter、默认值、大小限制、参数渲染、UTF-8、二进制、目录逃逸和符号链接。
- Catalog：三级覆盖、稳定截断、命令冲突、工具校验、用户正文刷新和项目缓存。
- 信任：首次确认、拒绝、内容变化、项目隔离、原子写入及权限失败。
- 启动：有/无 MCP、MCP 工具引用、输入启用时机、Registry 冻结、16 条默认命令。
- Prompt：稳定 Catalog、动态 Active、同一 Agent Loop 下一迭代可见、失败不污染状态。
- 历史：快照编解码、日志降级、旧日志、clear、resume 和失效 Skill warning。
- Fork：none/recent、严格工具过滤、Plan/default/bypass、审批、嵌套加载、成功、Provider/工具/迭代失败、Ctrl+C 取消、主历史成对和 Token 统计。
- 包构建：wheel 中包含三个内置 `SKILL.md`。
- 回归：`uv run pytest`、`uv run ruff check .`，format 只检查本次文件。
- 端到端：在 tmux 中运行 `uv run kcode`，验证项目信任、帮助、列表、参数、fork、自然语言激活、clear、resume 和退出。

## Assumptions

- 本实现基于已提交的 Slash Command（`bfb14e2`）及其既有长期记忆能力继续开发。
- `commit`、`test` 为 inline；`review` 为 fork + `none`。
- 空 `allowed_tools` 表示不额外限制；权限系统始终是最终边界。
- 显式 Skill 命令不会自动激活。
- JSONL 保持 schema 1，无数据迁移。
- `KCODE_SKILL_TRUST_PATH` 仅作为显式路径覆盖；未设置时使用 `~/.kcode/skill-trust.json`。
- 第一阶段不实现脚本、附件、远程安装、热加载、模型覆盖和 `fork_context: full`。
- 已批准 Spec 的 F1–F23 均有对应模块与测试，没有遗留实现决策。
