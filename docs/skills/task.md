# Kcode Skill MVP + Fork Tasks

> 所有任务基于已批准的 `spec.md` 与 `plan.md`。实现时保留工作区已有未提交改动，只修改本文件列出的目标位置。测试命令统一使用 `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache`，避免默认 uv 缓存目录权限影响结果。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/kcode/skills/__init__.py` | 导出 Skill 公共类型与入口 |
| 新建 | `src/kcode/skills/models.py` | 元数据、定义、来源、模式与结果模型 |
| 新建 | `src/kcode/skills/parser.py` | 安全读取、严格 frontmatter 解析与参数渲染 |
| 新建 | `src/kcode/skills/catalog.py` | 三级发现、覆盖、预算、工具与命令校验、正文刷新 |
| 新建 | `src/kcode/skills/trust.py` | 项目指纹、信任请求与独立信任存储 |
| 新建 | `src/kcode/skills/runtime.py` | Active Skills、预算、恢复与状态快照 |
| 新建 | `src/kcode/skills/tools.py` | `load_skill` 工具 |
| 新建 | `src/kcode/skills/executor.py` | inline/fork 分派、recent 历史、事件和结果回流 |
| 新建 | `src/kcode/skills/builtin/*/SKILL.md` | `commit`、`review`、`test` 内置 Skill |
| 新建 | `src/kcode/ui/skill_trust.py` | 项目 Skill 信任确认界面 |
| 修改 | `src/kcode/tools/base.py`、`src/kcode/tools/registry.py` | always-visible 与受限 Registry |
| 修改 | `src/kcode/commands/models.py`、`builtins.py`、`registry.py` | Skill Host 接口、`/skill`、动态命令与延迟冻结 |
| 修改 | `src/kcode/history/models.py`、`codec.py`、`journal.py`、`store.py`、`runtime.py` | `skill_state` 写入、读取和恢复 |
| 修改 | `src/kcode/prompting/sections.py`、`src/kcode/orchestration.py` | Available/Active Prompt 和外部轮次提交 |
| 修改 | `src/kcode/ui/app.py`、`src/kcode/cli.py` | 统一启动、执行、取消、clear/resume 与 UI 用量 |
| 修改 | `pyproject.toml` | 确认内置 Markdown 资源进入 wheel |
| 新建 | `tests/test_skill_parser.py` | 解析、路径和参数渲染测试 |
| 新建 | `tests/test_skill_catalog.py` | 覆盖、预算、校验和刷新测试 |
| 新建 | `tests/test_skill_trust.py` | 指纹与信任存储测试 |
| 新建 | `tests/test_skill_runtime.py` | Active 状态与 LoadSkill 测试 |
| 新建 | `tests/test_skill_executor.py` | inline/fork 行为测试 |
| 新建 | `tests/test_skill_packaging.py` | wheel 内置资源测试 |
| 修改 | `tests/test_tools.py`、`tests/test_commands.py`、`tests/test_agent_loop.py` | 公共接口与 Prompt 集成回归 |
| 修改 | `tests/test_history_*.py`、`tests/test_app.py`、`tests/test_mcp_ui.py`、`tests/test_resume_ui.py` | 状态历史、启动及 TUI 集成回归 |

## T1：建立 Skill 领域模型

**文件：** `src/kcode/skills/models.py`、`src/kcode/skills/__init__.py`、`tests/test_skill_parser.py`
**依赖：** 无

**步骤：**

1. 定义 `SkillSource`、`SkillMode`、`ForkContext`、`SkillMeta` 和 `SkillDefinition`。
2. 用 Pydantic 严格限制字段、类型、名称、描述、默认值和 inline/fork 组合。
3. 写模型测试，覆盖合法最小值、未知字段、非法名称、重复工具、多行描述和非法枚举。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_parser.py -q` 通过。

## T2：实现受限文件读取

**文件：** `src/kcode/skills/parser.py`、`tests/test_skill_parser.py`
**依赖：** T1

**步骤：**

1. 在读取内容前使用 `lstat`/真实路径检查候选目录、`SKILL.md`、普通文件和根目录边界。
2. 在解析前执行 32 KiB、UTF-8、NUL/二进制检查。
3. 将失败统一转换为不包含正文的结构化 warning。
4. 添加符号链接、边界逃逸、缺失文件、非法 UTF-8、二进制和精确大小边界测试。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_parser.py -q` 通过。

## T3：实现 frontmatter 与正文解析

**文件：** `src/kcode/skills/parser.py`、`tests/test_skill_parser.py`
**依赖：** T1、T2

**步骤：**

1. 只接受由 `---` 包围的 YAML frontmatter 和非空 Markdown 正文。
2. 使用 `yaml.safe_load`，要求解析结果为映射，再交给严格元数据模型。
3. 计算启动期原始内容摘要并构造 `SkillDefinition`。
4. 添加空正文、缺失分隔符、非映射 YAML、未知字段和合法默认值测试。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_parser.py -q` 通过。

## T4：实现参数渲染

**文件：** `src/kcode/skills/parser.py`、`tests/test_skill_parser.py`
**依赖：** T3

**步骤：**

1. 替换正文中的全部 `$ARGUMENTS`。
2. 无占位符且参数非空时追加 `## User Request`；参数为空时不追加。
3. 为 inline/fork 共用同一个渲染入口，并加入工具建议头部但不改变原始参数。
4. 覆盖四种参数组合、多占位符和换行参数测试。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_parser.py -q` 通过。

## T5：实现项目指纹

**文件：** `src/kcode/skills/trust.py`、`tests/test_skill_trust.py`
**依赖：** T2

**步骤：**

1. 规范化项目路径，按相对路径排序候选 `SKILL.md`。
2. 用项目路径、相对路径和原始字节生成 SHA-256。
3. 拒绝符号链接或越界候选，不把正文放入信任请求展示字段。
4. 测试遍历顺序稳定、内容/文件名/项目路径变化和非法候选。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_trust.py -q` 通过。

## T6：实现独立信任存储

**文件：** `src/kcode/skills/trust.py`、`tests/test_skill_trust.py`
**依赖：** T5

**步骤：**

1. 默认使用 `~/.kcode/skill-trust.json`，按规范化项目路径保存指纹。
2. 实现安全读取、原子替换、父目录 `0700` 和文件 `0600` 权限收紧。
3. 文件损坏、符号链接、权限失败时返回安全失败，不能自动信任。
4. 添加首次、已信任、变化、项目隔离、损坏和写入失败测试。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_trust.py -q` 通过。

## T7：实现三级 Catalog 发现与覆盖

**文件：** `src/kcode/skills/catalog.py`、`tests/test_skill_catalog.py`
**依赖：** T3、T6

**步骤：**

1. 发现内置、用户和项目 Skill 目录，保证遍历顺序稳定。
2. 只有已批准项目集合参与解析；拒绝后保留内置和用户集合。
3. 按内置 `<` 用户 `<` 项目覆盖同名定义。
4. 添加三级覆盖、项目拒绝、单候选损坏不阻断和空 Catalog 测试。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_catalog.py -q` 通过。

## T8：实现 Catalog 预算与最终校验

**文件：** `src/kcode/skills/catalog.py`、`tests/test_skill_catalog.py`
**依赖：** T7

**步骤：**

1. 覆盖完成后按名称排序，保留前 30 项并为其余项 warning。
2. 用最终 Tool Registry 名称校验 `allowed_tools`。
3. 用内置命令名称和别名校验动态命令冲突。
4. 确保失败项从列表、Prompt 和动态命令候选共同消失。
5. 添加边界、未知本地/MCP 工具和命令/别名冲突测试。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_catalog.py -q` 通过。

## T9：实现调用前正文刷新

**文件：** `src/kcode/skills/catalog.py`、`tests/test_skill_catalog.py`
**依赖：** T8

**步骤：**

1. 内置和项目 Skill 返回启动期缓存。
2. 用户 Skill 每次调用安全重读；仅元数据完全一致时采用新正文。
3. 用户读取失败或元数据变化时回退缓存并 warning。
4. 项目文件摘要变化时继续缓存并返回重启重新信任 warning。
5. 添加用户刷新、回退、项目变化和正文不泄漏测试。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_catalog.py -q` 通过。

## T10：增加 always-visible 与受限 Tool Registry

**文件：** `src/kcode/tools/base.py`、`src/kcode/tools/registry.py`、`tests/test_tools.py`
**依赖：** 无

**步骤：**

1. 为 `ToolSpec` 增加默认关闭的 `always_visible`。
2. 为 Registry 增加名称快照和 `restricted_view(names)`。
3. 受限视图共享工具实例、保留 always-visible 工具，并继续拒绝重复注册。
4. 测试空/部分/全部白名单、always-visible 和执行查询一致性。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_tools.py tests/test_tool_executor.py -q` 通过。

## T11：增加命令延迟冻结能力

**文件：** `src/kcode/commands/registry.py`、`src/kcode/commands/models.py`、`src/kcode/commands/builtins.py`、`tests/test_commands.py`
**依赖：** 无

**步骤：**

1. 暴露只读冻结状态，并让 `freeze()` 幂等。
2. 为 `create_builtin_registry` 增加默认保持兼容的冻结参数。
3. 删除硬编码 `/review` 和固定 Prompt；定义轻量 `SkillSummary` 与 Host 列表接口，注册本地 `/skill`。
4. 测试默认冻结、延迟冻结、重复冻结、12 个原有非 Skill 命令和新增 `/skill`。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_commands.py tests/test_command_menu.py -q` 通过。

## T12：注册动态 Skill 命令

**文件：** `src/kcode/commands/models.py`、`src/kcode/commands/builtins.py`、`src/kcode/commands/__init__.py`、`tests/test_commands.py`
**依赖：** T8、T11

**步骤：**

1. 扩展 `CommandHost` 的显示文本提交和 Skill 执行接口。
2. 提供按 Catalog 注册可选参数 `CommandSpec` 的函数，handler 只调用 Host Skill 入口。
3. 保持 `/help <skill>`、排序、详情和补全复用现有 Registry。
4. 测试三个 Skill 后共 16 条命令、参数原样传递和冲突项缺失。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_commands.py tests/test_command_menu.py -q` 通过。

## T13：扩展 Skill 状态日志模型

**文件：** `src/kcode/history/models.py`、`src/kcode/history/codec.py`、`tests/test_history_codec.py`
**依赖：** 无

**步骤：**

1. 新增严格的 `SkillStateRecord(type="skill_state", ts, names)`。
2. 加入 JournalRecord 判别联合，保持 Session/Message/SessionEnd 和 schema 1 不变。
3. 测试有序名称编解码、非法名称列表、未知旧行处理预期和原消息编码不变。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_history_codec.py -q` 通过。

## T14：写入并读取最后 Skill 快照

**文件：** `src/kcode/history/journal.py`、`src/kcode/history/store.py`、`src/kcode/history/models.py`、`tests/test_history_journal.py`、`tests/test_history_store.py`
**依赖：** T13

**步骤：**

1. 为 Journal 增加异步追加完整 Skill 名称快照的方法，复用降级和脱敏路径。
2. 为 `LoadedSession` 增加最后快照名称。
3. Store 扫描记录时更新最后快照，不把它计作消息或标题。
4. 测试多快照取最后值、无快照为空、损坏行跳过和写入降级。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_history_journal.py tests/test_history_store.py -q` 通过。

## T15：把 Active 名称接入 Session Runtime

**文件：** `src/kcode/history/runtime.py`、`tests/test_history_runtime.py`
**依赖：** T14

**步骤：**

1. 为 `SessionRuntime` 增加有序 `active_skill_names` 和写快照入口。
2. fresh session 初始化为空，resume runtime 使用 `LoadedSession` 最后快照。
3. 保持 clear/new session、resume reminder 和 ContextManager 构造不变。
4. 测试新会话为空、恢复名称、clear 清空和旧会话兼容。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_history_runtime.py tests/test_session_persistence_integration.py -q` 通过。

## T16：实现 Active Skill Runtime

**文件：** `src/kcode/skills/runtime.py`、`tests/test_skill_runtime.py`
**依赖：** T9、T15

**步骤：**

1. 维护按激活顺序排列的定义，不复制 Catalog 所有正文。
2. 实现最多 5 项、总正文 64 KiB 的原子预算检查。
3. 重复激活刷新正文并保持顺序；失败不改变旧状态。
4. 激活后调用当前 Session Runtime 写完整名称快照；写失败作为 warning 返回。
5. 测试边界、幂等、回滚、日志降级和名称顺序。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_runtime.py -q` 通过。

## T17：实现 Active 恢复与 Prompt 渲染

**文件：** `src/kcode/skills/runtime.py`、`tests/test_skill_runtime.py`
**依赖：** T16

**步骤：**

1. 从名称列表按当前 Catalog 重载，不重新写快照。
2. 缺失、失效或预算超限项逐项跳过并 warning。
3. 渲染 `## Active Skills`，按顺序包含名称、工具建议和正文。
4. 确保 warning 和名称快照均不包含正文。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_runtime.py -q` 通过。

## T18：实现 LoadSkill 工具

**文件：** `src/kcode/skills/tools.py`、`src/kcode/skills/__init__.py`、`tests/test_skill_runtime.py`
**依赖：** T10、T16

**步骤：**

1. 定义只接受 `name` 的严格参数模型。
2. ToolSpec 使用 `read_only` 和 `always_visible=True`。
3. 执行时调用 Runtime 激活；成功结果只含名称和 Active 名称，失败使用结构化错误。
4. 测试正文不出现在结果、Plan Mode 可见和预算失败不污染状态。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_runtime.py tests/test_tool_executor.py -q` 通过。

## T19：加入 Available 与 Active Prompt

**文件：** `src/kcode/prompting/sections.py`、`src/kcode/orchestration.py`、`tests/test_prompting.py`、`tests/test_agent_loop.py`
**依赖：** T17、T18

**步骤：**

1. 将稳定占位 section 定义为 `available_skills`，保持唯一优先级和 Prompt Cache 稳定顺序。
2. 为 Runner 增加绑定 Skill Runtime 和更新 Catalog Prompt 的空闲期接口。
3. 每次 Agent 迭代用基础 Environment 加当前 Active Prompt，不修改固定 System Prompt。
4. 测试首轮只含名称/描述、激活前无正文、LoadSkill 后下一迭代含正文。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_prompting.py tests/test_agent_loop.py -q` 通过。

## T20：增加主 Runner 外部轮次提交

**文件：** `src/kcode/orchestration.py`、`tests/test_agent_loop.py`
**依赖：** T15

**步骤：**

1. 增加仅在 Runner 空闲时调用的完整 user/assistant 轮次提交接口。
2. 统一写 Conversation、Session Journal 和长期记忆候选。
3. 返回持久化或记忆排队 warning，不修改主 ContextManager 用量锚。
4. 测试成功、Journal 降级、运行中拒绝和记忆候选内容。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_agent_loop.py tests/test_session_persistence_integration.py -q` 通过。

## T21：实现 inline Skill 分派

**文件：** `src/kcode/skills/executor.py`、`tests/test_skill_executor.py`
**依赖：** T4、T9、T20

**步骤：**

1. 获取调用时正文并渲染参数。
2. inline 返回模型 Prompt、简短 `/<name> args` 标签和 warning，不激活 Skill。
3. 保留 `allowed_tools` 为 Prompt 建议，不创建受限 Registry。
4. 测试正文刷新、参数、显示分离、一次性执行和状态不变。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_executor.py -q` 通过。

## T22：选择 recent 纯文本历史

**文件：** `src/kcode/skills/executor.py`、`tests/test_skill_executor.py`
**依赖：** T21

**步骤：**

1. 从主 Conversation canonical messages 识别没有工具调用/结果的完整 user-assistant 对。
2. `recent` 复制最后两对，`none` 返回空历史。
3. 不截断工具链，不复制 continuation state 或系统消息。
4. 测试少于两轮、混合工具轮次、孤立消息保护和顺序。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_executor.py -q` 通过。

## T23：构造受限 Fork Runner

**文件：** `src/kcode/skills/executor.py`、`tests/test_skill_executor.py`
**依赖：** T10、T18、T19、T22

**步骤：**

1. 按 Skill 白名单创建受限 Registry；空白名单使用全部工具，再由权限模式过滤定义。
2. 子 Runner 复用 Provider、权限引擎、LocalPermissionStore、审批器、ToolContext 和 AgentConfig。
3. 创建独立 Conversation、ContextManager 和 SkillRuntime，并注册专用 LoadSkill。
4. 测试白名单外工具不可见且不可执行、always-visible 可用、模式和配置继承。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_executor.py tests/test_permissions.py -q` 通过。

## T24：实现 Fork 结果回流

**文件：** `src/kcode/skills/executor.py`、`tests/test_skill_executor.py`
**依赖：** T20、T23

**步骤：**

1. 转发子 Runner 进度、工具、审批和 Token 事件。
2. 完成时取得最终 assistant 文本并调用主 Runner 外部轮次提交。
3. Provider、未知工具上限、无效响应和迭代上限规范化为失败 assistant 文本并成对回流。
4. Ctrl+C 只取消子 Runner，不写主 Conversation 或 Journal。
5. 测试成功、各失败、取消、主历史成对和子用量不改变主锚。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_executor.py -q` 通过。

## T25：添加三个内置 Skill

**文件：** `src/kcode/skills/builtin/commit/SKILL.md`、`review/SKILL.md`、`test/SKILL.md`、`tests/test_skill_catalog.py`
**依赖：** T3

**步骤：**

1. 将提交、审查和测试 SOP 写成严格合法 frontmatter 与 Markdown。
2. `commit`、`test` 设为 inline；`review` 设为 fork/none。
3. 只声明当前真实本地工具名；正文使用 `$ARGUMENTS` 接收关注点。
4. 测试内置 Catalog、模式、工具名和参数渲染。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_catalog.py tests/test_skill_parser.py -q` 通过。

## T26：实现项目 Skill 信任界面

**文件：** `src/kcode/ui/skill_trust.py`、`tests/test_app.py`
**依赖：** T5、T6

**步骤：**

1. 参照 MCP 信任界面显示项目路径、Skill 名称和安全说明，不显示正文或完整指纹。
2. 提供批准、拒绝和关闭三种结果；关闭按拒绝处理。
3. 添加 Textual pilot 测试，验证键盘操作和敏感内容不显示。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_app.py -q` 通过。

## T27：统一 Skill/MCP 启动收尾

**文件：** `src/kcode/ui/app.py`、`src/kcode/cli.py`、`tests/test_app.py`、`tests/test_mcp_ui.py`
**依赖：** T8、T12、T18、T19、T26

**步骤：**

1. CLI 创建未冻结命令 Registry、Skill Catalog Builder、Runtime 和 LoadSkill。
2. TUI 无论有无 MCP 都在挂载时禁用输入并进入同一启动 worker。
3. 完成项目信任、MCP 注册、最终校验、Catalog Prompt、动态命令和 Registry freeze。
4. 任一步失败时按安全默认降级并最终恢复可输入状态；warning 不泄漏正文。
5. 测试无 MCP、有 MCP、MCP 失败、MCP Skill 工具引用和输入启用时机。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_app.py tests/test_mcp_ui.py tests/test_mcp_integration.py -q` 通过。

## T28：接入 Skill 命令与显示分离

**文件：** `src/kcode/ui/app.py`、`tests/test_app.py`、`tests/test_commands.py`
**依赖：** T21、T24、T27

**步骤：**

1. 实现 `command_submit_user(model_text, display_text)` 和 `command_execute_skill`。
2. inline 使用主生成 worker，UI 仅显示命令标签，Runner 收到完整 Prompt。
3. fork 使用 Skill Executor 事件流，显示标签、工具进度和最终结果。
4. 保持普通文本和 `/plan [任务]` 的现有行为。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_app.py tests/test_commands.py -q` 通过。

## T29：接入 Fork 取消和用量

**文件：** `src/kcode/ui/app.py`、`tests/test_app.py`
**依赖：** T24、T28

**步骤：**

1. 让 App 跟踪当前实际运行的主或子 Runner。
2. Ctrl+C 取消当前 Runner，并关闭当前审批界面。
3. 子 TokenUsageUpdated 更新请求和 session UI 统计；不调用主 ContextManager 记账。
4. 测试 fork 运行中取消、审批中取消、输入恢复和 session Token 累计。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_app.py tests/test_skill_executor.py -q` 通过。

## T30：接入 Clear 与 Resume

**文件：** `src/kcode/ui/app.py`、`tests/test_resume_ui.py`、`tests/test_app.py`
**依赖：** T15、T17、T27

**步骤：**

1. `/clear` 后将 Skill Runtime 绑定到新 Session Runtime 的空名称列表。
2. `/resume` 后按恢复名称调用当前 Catalog 重载，再绑定 Runner。
3. 将缺失、失效和未信任 warning 显示给用户，不写新快照。
4. 测试多 Skill 恢复、旧日志、缺失项和 clear 后为空。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_resume_ui.py tests/test_app.py tests/test_history_runtime.py -q` 通过。

## T31：验证内置资源打包

**文件：** `pyproject.toml`、`tests/test_skill_packaging.py`
**依赖：** T25

**步骤：**

1. 配置 Hatch 将三个 `SKILL.md` 作为包资源包含在 wheel。
2. 测试通过 `importlib.resources` 在源码环境读取三个内置文件。
3. 构建 wheel 并检查压缩包成员包含三个资源。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv build --wheel` 成功，随后 `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_packaging.py -q` 通过。

## T32：运行 Skill 定向回归

**文件：** 本功能全部新增和修改文件
**依赖：** T1–T31

**步骤：**

1. 运行全部 Skill、Command、History、Agent 和 UI 定向测试。
2. 修复任何失败，不放宽 Spec 安全边界或删除覆盖用例。
3. 确认 `git diff --check` 无空白错误，并检查 diff 未包含无关用户文件。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_parser.py tests/test_skill_catalog.py tests/test_skill_trust.py tests/test_skill_runtime.py tests/test_skill_executor.py tests/test_skill_packaging.py tests/test_commands.py tests/test_history_codec.py tests/test_history_journal.py tests/test_history_store.py tests/test_history_runtime.py tests/test_agent_loop.py tests/test_app.py tests/test_mcp_ui.py tests/test_resume_ui.py -q` 全部通过，`git diff --check` 无输出。

## T33：运行全仓回归与静态检查

**文件：** 本功能全部新增和修改文件
**依赖：** T32

**步骤：**

1. 运行全仓 pytest，确认结果不低于当前 `291 passed, 2 skipped` 基线且新增测试全部计入。
2. 运行 Ruff lint。
3. 仅对本功能新增和修改的 Python 文件运行 Ruff format check，不要求修复 15 个既有格式差异文件。
4. 记录真实通过数量和任何 skip 原因，供 Checklist 验收使用。

**验证：** `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest` 与 `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run ruff check .` 通过；本次 Python 文件的 `ruff format --check` 通过。

## T34：执行 tmux 端到端场景

**文件：** 无新增文件；使用当前工作区运行应用
**依赖：** T33

**步骤：**

1. 在临时项目准备用户和项目 Skill，用 `KCODE_SKILL_TRUST_PATH` 指向临时信任文件，启动 `uv run kcode` 并确认项目信任；禁止修改 `HOME`。
2. 实跑 `/help`、`/skill`、`/commit 参数`、fork `/review` 和自然语言触发 `load_skill`。
3. 实跑 `/clear`、`/resume` 和退出，检查 Active 恢复、UI 标签、历史和取消行为。
4. 保存命令输出与 JSONL 可观测证据；删除临时测试数据，不修改用户真实 `~/.kcode`。

**验证：** Checklist 中所有端到端场景均有 tmux 输出或 session JSONL 证据，且真实用户配置未被修改。

## 执行顺序

```text
T1 → T2 → T3 → T4
      └→ T5 → T6 → T7 → T8 → T9
T10 ───────────────────────┐
T11 → T12                  │
T13 → T14 → T15 → T16 → T17 → T18 → T19
                         T15 → T20
T4 + T9 + T20 → T21 → T22 → T23 → T24
T3 → T25 → T31
T5 + T6 → T26
T8 + T12 + T18 + T19 + T26 → T27
T21 + T24 + T27 → T28 → T29
T15 + T17 + T27 → T30
T1–T31 → T32 → T33 → T34
```

## 自检结果

- Plan 的解析、Catalog、信任、Prompt、Command、Active、History、inline、fork、UI、打包和验收均有对应任务。
- T1–T34 均包含具体文件、依赖、操作步骤和可运行验证命令。
- 依赖图无循环；跨模块集成在基础类型和单元行为完成后进行。
- 文档没有占位标记、未决选项或其它语言项目的旧测试命令。
- 所有类型名与已批准 Plan 一致；没有加入 Spec 明确排除的脚本执行、安装、热加载、模型覆盖或 full fork。
