# Kcode Skill 技能包系统 Spec

## 背景

Kcode 已经具备 Slash Command、Agent Loop、本地与 MCP 工具、权限系统、稳定 System Prompt、会话持久化和长期记忆。当前 `/review` 仍把一段固定 Prompt 写在源码中，用户无法通过项目文件复用或覆盖团队流程；把所有操作规范放进 `KCODE.md` 又会让每轮请求携带无关内容。

本期引入 Skill 技能包：把一类可复用 AI 工作流写进目录中的 `SKILL.md`，启动时只暴露名称和描述，需要时再加载完整 SOP。系统同时支持当前对话内执行和隔离上下文执行，但不在本期引入第三方脚本或远程安装。

## 目标

- 将代码审查、提交和测试流程从源码中的固定 Prompt 迁移为可编辑、可覆盖的 Skill。
- 允许用户通过 Slash Command 显式调用 Skill，也允许 Agent 根据自然语言意图按需激活 Skill。
- 用渐进式加载减少无关上下文，并在隔离执行时真正收窄可见工具。
- 让项目级 Skill 在进入 Prompt 前经过用户信任确认。
- 保持权限、会话恢复、取消、Token 展示、长期记忆和 MCP 行为兼容。

## 功能需求

### Skill 定义与限制

- **F1：目录与格式。** 每个 Skill 是一个目录，必须包含 `SKILL.md`。文件由 YAML frontmatter 和非空 Markdown 正文组成；本期忽略目录中的其它文件。
- **F2：严格元数据。** frontmatter 只允许 `name`、`description`、`allowed_tools`、`mode`、`fork_context`。`name` 和 `description` 必填；`allowed_tools` 默认空数组，`mode` 默认 `inline`，`fork_context` 默认 `none`。`name` 必须匹配 `^[a-z][a-z0-9-]*$` 且不超过 32 个字符；`description` 必须是最多 200 个字符的非空单行文本；`allowed_tools` 必须是不重复的工具名数组；`mode` 只能是 `inline` 或 `fork`；`fork_context` 只能是 `none` 或 `recent`。inline Skill 如果显式设置非 `none` 的 `fork_context` 则无效。未知字段、非法类型和非法枚举值使该 Skill 整体失效，不能静默回退。
- **F3：资源预算。** 单个 `SKILL.md` 最大 32 KiB；最终 Catalog 最多包含 30 个 Skill；同时激活最多 5 个 Skill，激活正文合计最大 64 KiB。超过限制时跳过加载或拒绝激活，并给出不包含正文内容的明确原因。
- **F4：参数渲染。** 显式调用参数替换正文中所有 `$ARGUMENTS`；正文没有该占位符且参数非空时，在末尾追加 `## User Request` 和原始参数；参数为空时不追加额外章节。

### Catalog、覆盖与信任

- **F5：三级来源。** 启动时依次发现内置、用户级 `~/.kcode/skills/`、项目级 `<项目>/.kcode/skills/`。同名 Skill 后者覆盖前者，最终优先级为项目级高于用户级、用户级高于内置。
- **F6：项目级信任。** 项目级 Skill 的规范化项目路径、Skill 清单与文件内容共同形成项目专属指纹，并与其它信任信息分开存入 `~/.kcode/skill-trust.json`。首次发现或内容变化时，必须在输入可用前请求用户确认。拒绝或无法安全读写信任记录时，Kcode 继续启动，但排除全部项目级 Skill；内置和用户级 Skill 不受影响。
- **F7：安全路径。** Skill 加载拒绝符号链接、越过所属 Skill 根目录的真实路径、非普通文件、无效 UTF-8 和二进制内容。单个候选失败只跳过该候选，不阻断其它 Skill。
- **F8：工具与命令校验。** Kcode 在本地及已获信任的 MCP 工具完成注册后校验 `allowed_tools`。引用未知工具，或 Skill 名称与任一内置命令名称或别名冲突时，跳过该 Skill 并显示 warning。完成校验前输入保持禁用；校验与 Skill 命令注册完成后命令集合被冻结。
- **F9：正文新鲜度。** 内置 Skill 使用随包分发的缓存正文；用户级 Skill 每次调用或激活时安全重读正文，失败时回退启动期缓存并 warning；项目级 Skill 启动后发生变化时继续使用已信任缓存并要求重启重新确认，不能执行未重新信任的内容。

### 内置与 Slash Command

- **F10：内置 Skill。** Kcode 随包提供 `commit`、`review`、`test` 三个合法 Skill。`commit` 与 `test` 使用 inline；`review` 使用 fork 且默认不携带主对话历史。它们只声明 Kcode 实际存在的工具名。
- **F11：命令入口。** 删除源码中的硬编码 `/review` Prompt 命令，新增本地 `/skill` 列表命令，并为每个有效 Skill 自动注册 `/<name> [参数]`。动态 Skill 参与 `/help`、详情查询和现有 Tab 补全；默认只有三个内置 Skill 时，`/help` 显示 16 条命令。
- **F12：列表输出。** `/skill` 按名称排序显示最终有效 Catalog 的名称和描述；Catalog 为空时显示明确空状态。列表不显示完整正文、信任指纹或内部路径。

### 渐进式加载与激活状态

- **F13：两阶段 Prompt。** 稳定 System Prompt 只包含最终 Catalog 的名称、描述和 `load_skill` 使用说明；未激活时不包含任何 Skill 正文。每轮模型请求的动态环境中按激活顺序加入 Active Skills 正文。
- **F14：LoadSkill。** Kcode 提供只读、始终对主 Agent 可见的 `load_skill` 工具。有效调用按需取得正文并激活 Skill；未知、失效或超过激活预算时返回结构化失败，不中断 Agent Loop，也不改变原激活列表。成功激活后，同一 Agent Loop 的下一次模型迭代必须看到完整 SOP。
- **F15：激活幂等。** 重复激活同名 Skill 更新其正文但保持原顺序，不重复占用名额。激活列表属于当前 session；新 session 初始为空。

### 会话持久化与恢复

- **F16：状态记录。** 每次成功激活后，会话日志追加当前激活 Skill 名称的完整快照，不重复保存正文。日志写入失败时内存激活仍生效，但会话进入现有降级状态并向用户提示。
- **F17：清理与恢复。** `/clear` 归档旧 session 并创建空激活列表的新 session。恢复历史 session 时读取最后一个激活快照，从当前已信任 Catalog 重新加载对应正文；缺失、未信任或失效的 Skill 被跳过并 warning。没有 Skill 状态记录的旧会话按空列表恢复。

### Inline 与 Fork 执行

- **F18：Inline。** inline Skill 把渲染后的 SOP 作为正常 user 消息交给主 Agent；界面可显示简短的命令标签。`allowed_tools` 在 inline 中只做启动期存在性校验和建议提示，实际权限仍由现有权限系统决定。
- **F19：Fork 上下文。** fork Skill 在独立内存对话中执行。`none` 不复制主历史；`recent` 只复制最近两个已经完成的纯文本用户—助手轮次，不复制或截断工具调用链。
- **F20：Fork 工具。** fork 中 `allowed_tools` 是真实可见性白名单；白名单为空表示不额外限制。子 Agent 仍经过现有权限系统。子 Agent 自己的 `load_skill` 始终可见，但嵌套激活不能扩大父 Skill 的工具白名单，也不能修改主 session 的激活状态。
- **F21：Fork 运行边界。** fork 继承主会话当前 Provider、模型、权限模式、审批器、工具限制和最大迭代数，不允许通过 fork 提升权限，也不支持 Skill 指定其它模型。
- **F22：Fork 回流。** fork 成功后，主会话保存“渲染后的 Skill user 消息 + 最终 assistant 结果”的完整一轮；界面只需显示简短调用标签和最终结果。Provider、工具或迭代失败保存同样成对的失败轮次；用户主动取消只显示取消状态，不写入主历史。
- **F23：用量与后处理。** fork Token 计入当前请求及 session 的 UI 用量统计，但不写入主对话的上下文估算锚。成功或非取消失败形成的主轮次按现有会话规则持久化，并在长期记忆启用时进入现有候选提取流程。

## 非功能需求

- **N1：安全默认。** 解析、路径、信任、依赖或预算信息不足时只能降低能力；不能自动信任项目 Skill、自动扩大工具白名单或绕过权限审批。
- **N2：启动一致性。** 有无 MCP 都走同一个 Skill 最终校验入口；输入框只在 MCP 初始化、Skill 校验、命令注册和 Prompt Catalog 更新完成后启用。
- **N3：Prompt 稳定性。** Catalog 位于稳定 Prompt 前缀；Active Skills 位于每轮重建的动态环境。激活变化不应修改固定 System Prompt 或破坏已有 Prompt Cache 前缀。
- **N4：历史兼容。** 新增 Skill 状态记录不能改变现有 user、assistant、tool_result 记录格式。旧 Kcode 可以跳过新记录；新 Kcode 可以恢复没有 Skill 状态的旧会话。
- **N5：秘密与错误。** warning、信任界面、工具结果和 session 状态不得包含 Skill 正文、API Key、URL 凭据或已加载敏感值；项目路径只在确有助于用户确认时展示。
- **N6：取消与故障隔离。** Skill 解析失败、项目拒绝信任、MCP 依赖缺失、LoadSkill 失败和 fork 失败都不能卡死 TUI 或破坏主 Agent Runner；取消遵循现有 Ctrl+C 行为。
- **N7：质量基线。** Python 3.11+、全部自动化测试和 Ruff lint 必须通过。本期新增和修改文件必须通过格式检查；不把工作区已有的全仓格式差异纳入本功能范围。
- **N8：现有行为兼容。** 除硬编码 `/review` 被 Skill 替换、增加 `/skill` 和动态 Skill 命令外，现有命令、权限模式、MCP 信任、会话恢复、长期记忆和工具结果协议不改变。

## 不做的事

- 不支持 `tool.json`、Skill 专属可执行脚本或自动执行附属资源。
- 不支持从 URL、zip、市场或仓库远程安装 Skill。
- 不支持运行时新增、删除或热重载 Skill；元数据变化需要重启。
- 不支持 `model` 字段、Provider 切换或任意模型覆盖。
- 不支持 `fork_context: full` 或为 fork 额外调用模型生成历史摘要。
- 不支持 Skill 间扩大工具权限、修改主 session 激活状态或显式委派权限图。
- 不提供 Skill 详情面板、版本锁文件、依赖解析、独立日志和云同步。
- 不清理当前工作区与本功能无关的未提交改动或既有格式差异。

## 验收标准

- **AC1（F1、F2）：** 合法最小 `SKILL.md` 可加载；未知字段、非法名称、非法枚举、空正文和多行描述分别只跳过对应 Skill，并产生不含正文的 warning。
- **AC2（F3）：** 32 KiB 文件、30 个 Catalog 项、5 个激活项和 64 KiB 激活正文的边界值可用；超过任一边界时安全拒绝且原状态不变。
- **AC3（F4）：** 有无 `$ARGUMENTS`、有无参数的四种组合均生成约定文本，原始参数不被命令解析器截断或改写。
- **AC4（F5）：** 同名 `commit` 同时存在于三个来源时最终使用项目版本；移除项目版本后重启使用用户版本，再移除后使用内置版本。
- **AC5（F6）：** 首次项目 Skill 出现时输入保持禁用并展示信任请求；批准后加载，拒绝后仅加载内置和用户 Skill；内容变化后重新请求信任。
- **AC6（F7）：** 符号链接、边界逃逸、二进制、非法 UTF-8 和缺失文件均被跳过，正常候选仍可使用。
- **AC7（F8）：** Skill 对 MCP 工具的合法引用在 MCP 注册后通过；未知工具和内置命令/别名冲突使 Skill 从 `/skill`、`/help` 和补全中共同消失；最终命令注册表被冻结。
- **AC8（F9）：** 修改用户级正文后下一次调用读取新正文；读取失败回退缓存并 warning；修改已信任项目正文后本进程仍只执行缓存内容并提示重启重新确认。
- **AC9（F10）：** 无用户和项目 Skill 时，`commit`、`review`、`test` 三个内置 Skill 可从安装后的包中加载，且其工具名全部通过实际 Registry 校验。
- **AC10（F11、F12）：** 默认启动后 `/help` 显示 16 条命令，原硬编码 `/review` 不再存在；`/skill`、详情查询、前缀补全和三个 Skill 参数调用输出正确；空 Catalog 显示空状态。
- **AC11（F13）：** 首轮稳定 Prompt 只出现名称和描述；激活前动态环境无正文，激活后同一 Loop 的下一轮出现对应 Active Skill 正文。
- **AC12（F14、F15）：** `load_skill` 在 Plan Mode 和任意 Skill 白名单下可见；未知名和预算超限失败不改变列表；重复激活更新正文、顺序和数量保持不变。
- **AC13（F16）：** 每次激活后 JSONL 出现最新名称快照；模拟日志写入失败时 Skill 仍在内存生效，同时显示持久化降级提示。
- **AC14（F17）：** 激活多个 Skill 后 `/clear` 的新 session 为空；恢复旧 session 可按最后快照恢复当前有效 Skill，缺失项被跳过；旧格式 session 恢复为空且不报致命错误。
- **AC15（F18）：** `/commit <说明>` 与 `/test <说明>` 走主 Agent 正常消息、权限、持久化和长期记忆流程；界面显示简短标签，模型收到完整渲染正文。
- **AC16（F19）：** fork `none` 看不到主历史；fork `recent` 只看到最近两个完整文本轮次，包含工具调用的历史不会产生孤立 tool_result。
- **AC17（F20、F21）：** fork 无法请求白名单外工具；嵌套 LoadSkill 不扩大白名单或改变主激活列表；Plan/default/bypass 下分别继承原权限模式和硬边界，Provider、模型及迭代上限不变。
- **AC18（F22）：** fork 成功和非取消失败均在主历史及 JSONL 中形成一对 user/assistant；Ctrl+C 取消不新增主历史，TUI 恢复可输入状态。
- **AC19（F23）：** fork 后 UI 请求与 session Token 增加，主对话的上下文估算锚不因子请求改变；完成轮次可恢复，并在长期记忆开启时进入既有候选流程。
- **AC20（N1-N8）：** 完整测试、Ruff lint 和本次改动文件格式检查通过；当前非 Skill 功能的回归测试保持通过，启动或错误输出不泄漏正文与敏感值。
