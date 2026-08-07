# KCode 上下文管理 Plan

## 架构概览

上下文管理位于 Agent Loop 与 Provider 之间。`Conversation` 继续保存当前进程内的规范消息记录；`ContextManager` 根据规范记录、当前未提交消息、工具集合和 usage 状态生成一次 Provider 实际可见的 `ContextSnapshot`。压缩只改变模型视图，不覆盖规范记录。

请求前的数据流固定为：

```text
规范记录 + 当前消息
        ↓
Artifact 外置与稳定替换
        ↓
预算估算
        ↓
必要时结构化摘要
        ↓
恢复段 + 近期原文
        ↓
ContextSnapshot
        ↓
Provider.stream
```

第一层只处理工具结果，不调用模型；第二层只处理模型视图，不删除 Artifact。手动压缩和紧急压缩复用第二层核心流程，但通过不同的触发原因控制阈值、熔断和 UI 文案。

## 核心数据结构

### `ArtifactRef`

表示已经落盘的工具结果：工具调用 ID、工具名、相对会话路径、字节数、结果状态、创建时间和脱敏标记。路径只允许指向当前会话 Artifact 根目录下的文件。

### `OffloadDecision`

表示一个工具调用 ID 的冻结决策：保留原文或使用 Artifact 替换体。替换体在首次成功生成后保存并复用，避免重复请求中的前缀发生无意义变化。

### `NormalizedUsage`

Provider 无关的 usage 快照，区分上下文输入 Token、输出 Token、缓存命中 Token、缓存写入 Token、是否为精确值和估算置信度。缓存字段不直接与已经包含缓存的输入字段重复相加。

### `ContextBudget`

表示模型上下文窗口、摘要输出预留、自动安全余量、手动安全余量、当前估算值和是否达到自动/紧急阈值。

### `CompactionState`

表示当前模型视图覆盖的规范记录范围、结构化摘要、摘要是否完整、保留的近期消息、恢复段来源和规范记录前缀指纹。规范记录发生新追加后，只将覆盖范围之后的新消息接入模型视图。

### `ContextSnapshot`

表示一次实际 Provider 请求：模型消息、同轮工具定义引用、预算估算、压缩原因、是否使用摘要、是否发生外置和估算置信度。

### `CompactionResult`

表示一次摘要尝试：新摘要、解析后的状态字段、覆盖范围、`history_incomplete`、压缩前后估算值、失败原因和重试次数。失败结果不得替换当前有效的 `CompactionState`。

## 模块设计

### `kcode.context.artifacts`

**职责：** 创建会话 Artifact 目录、写入脱敏工具结果、按范围读取内容、生成稳定预览。

**规则：** 文件路径为 `.kcode/sessions/<session_id>/tool-results/<tool_use_id>`；写入使用临时文件加原子替换；同一工具调用 ID 成功写入后不得重复写；写入失败不提交完成决策。

### `kcode.context.ledger`

**职责：** 保存 `tool_use_id` 到冻结决策和替换文本的映射。

**规则：** 账本的检查、选择、写入必须在同一临界区完成；按字节大小倒序选择，字节相同时按原始工具调用顺序选择；同一 ID 不得在本会话内翻转决策。

### `kcode.context.usage`

**职责：** 解析 Provider usage，维护上次精确上下文输入锚点，并对锚点之后的新增消息按 `字符数 / 3.5` 估算。

**规则：** Provider 适配器负责把原始字段转换成 `NormalizedUsage`；缺失或非法字段保持未知并降低置信度；预算计算单独预留输出空间。

### `kcode.context.compaction`

**职责：** 构造无工具摘要请求、解析结构化工作记忆、处理摘要自身过长的消息组重试。

**摘要调用：** 使用当前活动 Provider，传入历史和摘要指令，固定使用空工具集合与 `tool_choice="none"`。模型请求只保留文本结果；任何工具调用或无法解析的摘要均视为失败。

**摘要字段：** 目标、确认事实、推测、未知项、决策、文件与代码位置、错误与修复、当前状态、待办、下一步、Artifact 引用和 `history_incomplete`。

### `kcode.context.manager`

**职责：** 统一编排第一层、预算判断、第二层、恢复段、手动压缩、熔断和紧急恢复。

**对外操作：**

- 生成普通请求 `ContextSnapshot`；
- 记录 Provider 返回的归一化 usage；
- 记录成功文件读取和工具结果；
- 执行手动压缩；
- 处理一次 `prompt_too_long` 紧急恢复；
- 清空当前会话上下文状态。

### `Conversation` 集成

`Conversation` 的规范消息 API 继续返回完整当前进程历史。Context Manager 不直接删除或改写这些消息，而是保存模型视图的摘要、近期原文和来源范围。模型视图必须通过规范记录前缀指纹和覆盖位置校验，防止新一轮消息被错误重复或遗漏。

### `AgentRunner` 集成

每次迭代开始时先按权限模式计算一次工具定义，并将同一引用同时交给恢复段构造和普通 Provider 请求。随后调用 Context Manager 生成模型视图。工具执行成功后，先更新文件追踪和 Artifact 状态，再把规范结果追加到当前请求记录。

### TUI 与命令集成

新增 `/compact` 命令类型。命令只在 Agent 轮次空闲时执行，或者等待会话级互斥锁；它不写入规范对话、不发普通用户请求。UI 展示压缩原因、前后估算、置信度、Artifact 数量和历史完整性。

## 模块交互

### 普通请求

1. Runner 确定当前权限模式和工具定义。
2. Context Manager 从规范记录与当前消息构建输入。
3. Artifact 层处理单条和单批工具结果。
4. Usage 层计算上下文预算。
5. 达到自动阈值时调用 Compaction Engine；未达到则复用已有模型视图。
6. Manager 追加恢复段和近期原文，返回 ContextSnapshot。
7. Runner 用同一工具定义引用调用 Provider。
8. 收到 usage 后更新锚点；收到工具结果后更新文件追踪。

### 摘要失败

摘要失败时保留旧模型视图和规范记录，记录一次自动失败；连续失败三次后只关闭自动路径。手动 `/compact` 和紧急路径仍可尝试，但每次操作都有独立的一次性重试边界。

### Provider 撞墙

普通请求收到 `prompt_too_long` 后，Runner 调用一次紧急 Context Manager：强制第一层外置、执行第二层摘要、重新预算，然后最多重试原请求一次。第二次仍撞墙时结束当前请求，不进入递归重试。

## 文件组织

```text
kcode/
├── docs/context-management/
│   ├── spec.md
│   ├── plan.md
│   ├── task.md
│   └── checklist.md
├── src/kcode/context/
│   ├── __init__.py
│   ├── artifacts.py
│   ├── ledger.py
│   ├── usage.py
│   ├── compaction.py
│   ├── manager.py
│   └── models.py
└── tests/
    ├── test_context_artifacts.py
    ├── test_context_compaction.py
    ├── test_context_usage.py
    ├── test_context_manager.py
    └── test_context_integration.py
```

现有 `config.py`、`conversation.py`、`events.py`、`orchestration.py`、`providers/`、`ui/commands.py` 和 `ui/app.py` 只做必要集成修改；不移动既有权限、MCP 和工具实现。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 规范记录 | 保留完整当前进程历史 | 摘要失败或遗漏时可恢复，避免不可逆覆盖事实 |
| 模型视图 | Context Manager 独立维护 | 不污染 Conversation 与既有历史测试 |
| 第一层 | 确定性外置，不调用 LLM | 零模型费用、稳定、可测试 |
| 第二层摘要模型 | 沿用当前活动 Provider | 不新增配置和连接，行为与费用可预测 |
| 摘要工具 | 空工具集合与 `tool_choice="none"` | 摘要不应执行副作用操作，也避免工具定义冲突 |
| 摘要格式 | 结构化字段，兼容固定标题回退 | 支持不同 Provider，避免依赖特定结构化输出能力 |
| Token 估算 | Provider usage 锚定 + 增量近似 | 保留 MVP 简洁性，同时避免跨 Provider 字段重复计算 |
| Prompt Cache | 仅保持前缀稳定，不保证命中 | 缓存由 Provider 控制，不是正确性边界 |
| Artifact 写入 | 脱敏内容、原子写入、幂等路径 | 防止秘密泄漏和并发重复写入 |
| 并发 | 会话级锁保护模型视图，账本独立锁保护决策 | 避免 `/compact`、Runner 和紧急路径互相覆盖 |
