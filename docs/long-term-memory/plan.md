# KCode 长期记忆二期 Plan

## 架构概览

```text
AgentRunner 提交成功回答
        │
        ▼
MemoryCoordinator ──→ 本地信号检测
        │                    │ 未命中：结束
        │ 命中               ▼
        └────────────→ MemoryExtractor
                             │ 当前 Provider、无工具、严格 JSON
                             ▼
                     候选校验、脱敏、去重、持久化
                             │
                             ▼
                     TUI 空闲时人工审核
                             │
                  确认/编辑确认/拒绝
                             │
                             ▼
                       MemoryStore
                             │
                   重建索引并刷新 Prompt
```

- `AgentRunner` 只报告一轮已经可靠完成，不负责解析或写记忆。
- `MemoryCoordinator` 是长期记忆唯一协调者，管理后台任务、双作用域、候选队列、治理调度、Prompt 渲染和关闭。
- `MemoryStore` 只处理可信本地模型、路径安全、锁、原子写和索引重建。
- `MemoryExtractor` 与 `MemoryGovernor` 只能输出结构化建议，不能取得文件工具。
- `KCodeApp` 展示状态并收集用户决策，所有变更通过 Coordinator 执行。

## 配置设计

新增严格配置：

```yaml
memory:
  enabled: false
```

- `MemoryConfig.enabled` 默认 `false`。
- 只有用户级 `~/.kcode/config.yaml` 可以把功能开启。项目配置可以关闭，但不能把未同意的用户从关闭提升为开启；违规配置产生 warning。
- 提取、治理阈值和 Prompt 预算使用受测试的固定常量，不增加未成熟配置项。
- `config.example.yaml` 说明额外调用费用、本地明文存储和关闭方法。

## 核心数据结构

### MemoryType

- `user_preference`
- `feedback`
- `project_fact`
- `reference`

### MemoryScope

- `user`
- `project`

### MemoryStatus

- `active`
- `inactive`

### MemoryAction

- `create`
- `update`
- `merge`
- `inactivate`

类型系统中不定义供模型使用的永久删除动作。

### MemoryRecord

- `schema: int`：固定为 1。
- `id: str`：`mem_<uuid>` 格式的稳定标识。
- `type: MemoryType`
- `scope: MemoryScope`
- `status: MemoryStatus`
- `title: str`
- `summary: str`
- `application: str`：未来遇到什么情况时如何应用。
- `body: str`：经用户确认的补充说明。
- `source_session_id: str`
- `source_turn_hash: str`
- `created_at: float`
- `updated_at: float`

### MemoryProposal

- `schema: int`
- `id: str`：由来源哈希、动作、作用域和规范化内容确定。
- `action: MemoryAction`
- `type: MemoryType`
- `scope: MemoryScope`
- `target_ids: tuple[str, ...]`
- `title: str`
- `summary: str`
- `application: str`
- `body: str`
- `reason: str`
- `evidence: str`
- `source_session_id: str`
- `source_turn_hash: str`
- `created_at: float`

### CompletedTurn

- `session_id: str`
- `user_text: str`
- `final_text: str`
- `permission_mode: str`
- `turn_hash: str`

只包含允许进入提取请求的数据，不包含工具结果、thinking、摘要或 continuation state。

### MemoryState

- `schema: int`
- `last_governed_at: float | None`
- `completed_session_ids: tuple[str, ...]`
- `processed_proposal_hashes: tuple[str, ...]`：最多保留最近 500 条。

## 目录与文件组织

```text
~/.kcode/memory/
<workspace>/.kcode/memory/
├── entries/
│   └── mem_<uuid>.md
├── proposals/
│   └── proposal_<hash>.json
├── MEMORY.md
├── state.json
└── .memory.lock
```

代码组织：

```text
src/kcode/memory/
├── __init__.py      — 稳定导出
├── models.py        — 严格数据模型与常量
├── paths.py         — 双作用域路径与边界校验
├── store.py         — Markdown/JSON、锁、原子写、状态和索引
├── signals.py       — 无模型本地信号检测
├── extraction.py    — Provider 流收集与候选校验
├── governance.py    — 阈值判断与治理建议
├── prompting.py     — 精炼索引合并与预算控制
└── runtime.py       — MemoryCoordinator 与后台生命周期

src/kcode/ui/
└── memory.py        — MemoryReviewScreen、MemoryScreen、删除确认
```

## 模块设计

### MemoryStore

**职责：** 管理一个作用域的记录、候选、状态和索引。

**接口：**

- `load() -> MemorySnapshot`
- `save(record: MemoryRecord) -> None`
- `save_proposal(proposal: MemoryProposal) -> bool`
- `pending() -> tuple[MemoryProposal, ...]`
- `set_status(memory_id: str, status: MemoryStatus) -> MemoryRecord`
- `delete(memory_id: str) -> None`
- `resolve_proposal(proposal_id: str, decision_hash: str) -> None`
- `rebuild_index() -> IndexResult`
- `load_state() -> MemoryState`
- `save_state(state: MemoryState) -> None`

记录使用 YAML frontmatter + Markdown 正文。候选和状态使用规范化 JSON。所有写操作在 scope 锁内完成，拒绝根目录、锁、目标或临时文件的符号链接，并使用同目录临时文件、flush、fsync、权限收紧和 `os.replace`。

### MemorySignalDetector

**职责：** 用中英文确定性规则判断是否值得调用模型。

**接口：**

- `detect(turn: CompletedTurn) -> SignalResult`

识别明确记忆、偏好、纠正、稳定项目决定和参考资料信号。普通问答、一次性临时请求及失败轮次不进入提取。

### MemoryExtractor

**职责：** 复用当前 Provider，把允许的数据转换为结构化候选。

**接口：**

- `extract(turn: CompletedTurn, active_index: str) -> tuple[MemoryProposal, ...]`

调用使用 `tool_choice="none"`，最多接受三条候选。响应必须是单个纯 JSON 文档；多余文本、非法枚举、超长字段、未知更新目标或跨作用域动作均拒绝，不猜测修复。

### MemoryGovernor

**职责：** 对单个作用域的活跃精炼记录提出更新、合并或失效建议。

**接口：**

- `due(snapshot: MemorySnapshot, state: MemoryState, now: float) -> bool`
- `propose(records: Sequence[MemoryRecord]) -> tuple[MemoryProposal, ...]`

治理请求不接收跨作用域记录，没有永久删除动作。成功后才更新时间并清空完成会话计数；失败保留计数并节流。

### MemoryPromptRenderer

**职责：** 生成各 scope 索引及运行时合并内容。

**接口：**

- `render_index(records: Sequence[MemoryRecord]) -> str`
- `render_prompt(user_records, project_records) -> PromptMemoryResult`

只渲染 active 记录的类型、标题、摘要、应用方式和 ID。运行时合并固定为 24 KiB、最多 200 行；项目级冲突优先，两个 scope 保留最低配额后重新分配剩余空间。超预算只返回排除数量和 warning。

### MemoryCoordinator

**职责：** 连接 Store、提取器、治理器、Runner 与 TUI。

**接口：**

- `start() -> MemoryStartupResult`
- `submit_turn(turn: CompletedTurn) -> None`
- `pending() -> tuple[MemoryProposal, ...]`
- `apply(decision: MemoryDecision) -> MemoryApplyResult`
- `render_prompt() -> PromptMemoryResult`
- `session_closed(session_id: str, reason: str) -> tuple[str, ...]`
- `update_sensitive_values(values: Sequence[str]) -> None`
- `close() -> tuple[str, ...]`

Coordinator 使用单一后台队列串行运行记忆模型任务。候选先原子落盘再通知 UI。确认、编辑、拒绝、失效、恢复和永久删除都从同一入口执行并重新校验。

### AgentRunner

新增：

- `bind_memory(coordinator: MemoryCoordinator) -> None`
- `update_long_term_memory(content: str) -> None`

Runner 只在最终 `AssistantMessage` 已提交、会话 checkpoint 已尝试后登记不可变 `CompletedTurn`。后台登记不得延迟 `AgentStopped(COMPLETED)`。稳定系统提示词只能在 Runner 空闲时更新。

### SessionCloseListener

新增通用可选接口：

- `session_closed(session_id: str, reason: str) -> tuple[str, ...]`

`SessionCoordinator.clear()`、`resume()` 和 `close()` 在关闭旧会话后通知监听者。监听失败转成 warning，不阻断会话切换。MemoryCoordinator 只统计至少登记过一个成功轮次的 session，并按 ID 去重。

### TUI

- `MemoryReviewScreen` 返回确认、编辑确认或拒绝决策，并为 update/merge 展示旧值和新值差异。
- `MemoryScreen` 展示待审、active、inactive、治理建议和 warnings，并提供失效、恢复、审核和永久删除入口。
- `Ctrl+M` 是唯一新增入口。生成中或存在其他 Modal 时不抢占；候选只在界面空闲后自动弹出。

## 模块交互

### 启动

1. CLI 加载配置。
2. 若记忆关闭，不创建目录、不调用模型，按空内容构建 `long_term_memory`。
3. 若开启，创建 MemoryCoordinator，分别加载用户级和项目级 Store。
4. 坏文件转 warning，有效记录继续加载。
5. 渲染合并索引，通过 `SystemPromptBuilder.with_content("long_term_memory", content)` 构建初始 Prompt。
6. 将 Coordinator 绑定到 Runner、TUI 和 SessionCoordinator 关闭监听。

### 成功回答与候选

1. Runner 提交最终回答并尝试会话 checkpoint。
2. Runner 构造 CompletedTurn 并无等待地提交 Coordinator。
3. Coordinator 本地检测信号；未命中立即结束。
4. 命中后后台调用 Extractor，严格校验、脱敏并计算候选 ID。
5. 候选原子落盘后通知 TUI。
6. TUI 空闲时展示；决策回到 Coordinator。
7. 确认后写记录、处理候选、重建索引并刷新 Runner Prompt。

### 会话切换与治理

1. SessionCoordinator 关闭原 session 并通知监听者。
2. MemoryCoordinator 对有成功轮次且未计数的 session 计数。
3. 用户级和项目级分别检查治理条件。
4. 满足条件时后台生成建议并进入相同审核队列。
5. 进行中的候选保持原 session 来源，不绑定新 session。

### 关闭

1. Coordinator 停止接收新任务。
2. 取消尚未形成候选的 Provider 流。
3. 有界等待已经开始的磁盘提交。
4. 已落盘候选保留供下次启动审核，不允许半写或无限等待。

## 关键技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 启用方式 | 用户配置显式开启 | 避免升级后产生意外费用和明文留存 |
| 提取时机 | 本地信号命中后调用模型 | 降低费用和无效候选噪声 |
| 确认体验 | TUI 空闲时逐条弹窗 | 不打断回答、输入或工具授权 |
| 管理入口 | `Ctrl+M` 面板 | 提供可审计入口，不把记忆做成 slash command 集合 |
| 存储格式 | 每条 Markdown + YAML | 本地可读、可编辑、可审计，无新数据库依赖 |
| 项目记忆 Git 策略 | 默认忽略 | 防止隐私、错误结论和机器信息误提交 |
| Prompt 使用 | 注入精炼索引 | 二期不引入 RAG，同时控制上下文费用 |
| 模型选择 | 当前 Provider/模型 | 保持厂商无关，不新增凭据和配置面 |
| 删除策略 | 默认失效、永久删除二次确认 | 保持可恢复性和审计能力 |
| 治理策略 | 阈值触发、只提建议 | 控制费用并确保模型不能自主改写长期知识 |

## 失败边界

- 存储初始化失败：记忆整体降级关闭，聊天和一期会话存档继续。
- 单个 scope 失败：另一个 scope 仍可加载，界面明确标出不可用范围。
- Provider、JSON 或校验失败：只影响该次后台任务，不修改已有记忆。
- 索引刷新失败：保留旧 Prompt 和单条记录，显示持续 warning，下一次加载重建。
- 锁冲突：不覆盖其他进程数据，候选或操作留待重试。
- 会话切换：候选来源保持旧 session ID，不能写成新 session 来源。
- 关闭期间：未完成提取允许取消，已开始磁盘事务必须有界完成或安全失败。
