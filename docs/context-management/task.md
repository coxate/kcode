# KCode 上下文管理 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/kcode/context/models.py` | Artifact、usage、预算、模型视图和压缩结果模型 |
| 新建 | `src/kcode/context/artifacts.py` | Artifact 写入、读取、预览和会话目录 |
| 新建 | `src/kcode/context/ledger.py` | 外置决策冻结和并发账本 |
| 新建 | `src/kcode/context/usage.py` | Provider usage 归一化和增量估算 |
| 新建 | `src/kcode/context/compaction.py` | 无工具摘要请求、解析和 PTL 重试 |
| 新建 | `src/kcode/context/manager.py` | 上下文管理主编排器 |
| 新建 | `src/kcode/context/__init__.py` | 上下文模块公共导出 |
| 修改 | `src/kcode/config.py` | `context_window` 配置和默认解析 |
| 修改 | `src/kcode/providers/base.py`、`anthropic.py`、`openai.py` | 暴露模型窗口和归一化 usage 所需信息 |
| 修改 | `src/kcode/conversation.py` | 保持规范记录并支持模型视图来源范围 |
| 修改 | `src/kcode/orchestration.py` | 请求前上下文管理、usage 锚定和紧急重试 |
| 修改 | `src/kcode/ui/commands.py`、`src/kcode/ui/app.py` | `/compact` 路由和状态提示 |
| 新建 | `tests/test_context_*.py` | 上下文模块单元、集成和回归测试 |

## T1：建立上下文模型

**依赖：** 无

**步骤：**

1. 定义 ArtifactRef、OffloadDecision、NormalizedUsage、ContextBudget、CompactionState、ContextSnapshot 和 CompactionResult。
2. 为未知 usage、近似 Token 和历史不完整状态保留显式字段。
3. 保持现有 TokenUsage 与 AgentEvent 的构造方式兼容。

**验证：** 类型可导入；现有事件和 Provider 测试无需修改即可通过。

## T2：实现 ArtifactStore

**依赖：** T1

**步骤：**

1. 按会话 ID 创建 `.kcode/sessions/<session_id>/tool-results/`。
2. 使用脱敏后的结果写入以 `tool_use_id` 命名的文件。
3. 实现原子写入、重复写入跳过和范围读取。
4. 生成包含字节数、头部预览、路径、工具名和重读提示的稳定替换体。

**验证：** 测试大结果、UTF-8 字节计数、原子失败、重复写入和范围读取。

## T3：实现 OffloadLedger

**依赖：** T1、T2

**步骤：**

1. 记录每个工具调用 ID 的保留或外置决策。
2. 固定按字节倒序、原始顺序作为并列排序规则。
3. 用锁保护检查、决策和写入的同一临界区。
4. 保证失败不写完成决策，成功后复用原替换文本。

**验证：** 测试单条阈值、聚合阈值、稳定排序、决策冻结和并发调用。

## T4：接入第一层工具结果外置

**依赖：** T2、T3

**步骤：**

1. 在模型视图构造前扫描工具结果。
2. 先处理单条超限，再处理同批聚合超限。
3. 保留工具调用 ID、工具名、状态、警告和原始顺序。
4. 确认第一层不调用 Provider。

**验证：** 使用 Fake Provider 断言大结果处理不增加 LLM 请求。

## T5：实现 usage 归一化

**依赖：** T1

**步骤：**

1. 定义上下文输入、输出、缓存读写、精确性和置信度字段。
2. 为 Anthropic、OpenAI 和兼容 DeepSeek 的现有 usage 映射提供适配函数。
3. 明确避免把已经包含缓存的输入字段重复相加。
4. 对缺失或非法字段保留未知并降低置信度。

**验证：** 参数化测试各 Provider usage 组合、零值、缺失值和非法值。

## T6：实现增量预算估算

**依赖：** T5

**步骤：**

1. 保存上一次精确上下文输入 usage 作为锚点。
2. 对锚点之后新增消息按字符数除以 3.5 估算。
3. 分离上下文窗口、摘要输出预留和安全余量。
4. 实现显式配置、模型元数据和保守默认值的窗口解析顺序。

**验证：** 测试锚点替换而非累加、增量估算和阈值计算。

## T7：实现结构化摘要引擎

**依赖：** T1、T5、T6

**步骤：**

1. 构造摘要指令，要求只处理输入历史，不调用工具、不补全未提供事实。
2. 用当前活动 Provider 发起无工具摘要请求。
3. 解析目标、确认事实、推测、未知项、决策、文件、错误、当前状态、待办、下一步、Artifact 引用和完整性字段。
4. 拒绝空摘要、工具调用和无法解析的摘要结果。

**验证：** Fake Provider 捕获工具参数，断言为空；测试事实/推测/未知和摘要失败。

## T8：实现近期原文和恢复段

**依赖：** T1、T7

**步骤：**

1. 从消息尾部保留至少 10000 Token 且至少 5 条消息，直到历史起点为止。
2. 将工具调用和对应结果作为不可拆分消息组。
3. 恢复最近 5 个文件快照、同轮工具索引和固定边界提示。
4. 为快照增加路径、读取时间和截断标记。

**验证：** 测试消息边界、文件数量、快照截断和工具集合一致性。

## T9：实现 ContextManager 模型视图

**依赖：** T4、T6、T7、T8

**步骤：**

1. 区分规范记录和模型视图。
2. 保存压缩覆盖范围、来源指纹、摘要和近期原文状态。
3. 支持普通请求前自动压缩，并在摘要失败时保留旧视图。
4. 对新追加消息只接入已覆盖范围之后的内容，避免重复或遗漏。

**验证：** 测试压缩不修改 Conversation、跨轮次继续追加消息和压缩状态替换原子性。

## T10：集成 AgentRunner

**依赖：** T9

**步骤：**

1. 每轮开头只计算一次当前权限模式下的工具集合。
2. 在 `_collect` 前调用 ContextManager 生成 ContextSnapshot。
3. 将同一工具集合引用用于恢复段和 Provider 请求。
4. 请求完成后更新 normalized usage。
5. 工具完成后按顺序更新文件追踪、Artifact 状态和规范记录。

**验证：** Agent Loop 集成测试确认普通聊天、工具循环、权限模式、取消和历史提交不退化。

## T11：实现手动和异常路径

**依赖：** T9、T10

**步骤：**

1. 注册 `/compact`，确保未知命令仍不访问 Provider。
2. 为手动压缩绕过自动阈值和熔断，并增加会话级互斥。
3. 捕获 `prompt_too_long`，执行一次紧急压缩并最多重试原请求一次。
4. 实现自动摘要连续失败三次熔断，手动和紧急路径独立处理。

**验证：** UI、熔断、PTL、取消和并发测试全部覆盖。

## T12：补齐回归验收

**依赖：** T1-T11

**步骤：**

1. 增加压缩前后文件定位、错误恢复、待办延续和 Artifact 精确读取场景。
2. 增加用户约束、确认事实、推测和未知信息混合场景。
3. 运行 compileall、pytest、Ruff 和 git diff 检查。
4. 确认不修改用户已有无关改动和原始附件。

**验证：** 完整离线测试通过，记录每个验收场景的证据。

## 执行顺序

```text
T1 → T2 → T3 → T4
T1 → T5 → T6
T1/T5/T6 → T7 → T8 → T9
T4/T9 → T10 → T11 → T12
```
