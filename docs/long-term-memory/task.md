# KCode 长期记忆二期 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/kcode/memory/models.py` | 记忆、候选、决策、状态和快照模型 |
| 新建 | `src/kcode/memory/paths.py` | 用户级和项目级安全路径解析 |
| 新建 | `src/kcode/memory/store.py` | Markdown/JSON、锁、原子写、状态和索引 |
| 新建 | `src/kcode/memory/signals.py` | 中英文无模型信号检测 |
| 新建 | `src/kcode/memory/extraction.py` | Provider 流收集、纯 JSON 解析和候选校验 |
| 新建 | `src/kcode/memory/governance.py` | 治理阈值和建议生成 |
| 新建 | `src/kcode/memory/prompting.py` | 精炼索引与 Prompt 预算 |
| 新建 | `src/kcode/memory/runtime.py` | MemoryCoordinator 和后台生命周期 |
| 新建 | `src/kcode/memory/__init__.py` | 稳定公开接口 |
| 新建 | `src/kcode/ui/memory.py` | 候选审核、记忆面板和删除确认 |
| 修改 | `src/kcode/config.py`、`config.example.yaml` | 默认关闭配置、用户启用边界和说明 |
| 修改 | `src/kcode/orchestration.py` | 成功轮次登记和 Prompt 刷新 |
| 修改 | `src/kcode/history/runtime.py` | 通用会话关闭监听 |
| 修改 | `src/kcode/ui/app.py` | Ctrl+M、空闲审核、状态刷新和关闭 |
| 修改 | `src/kcode/cli.py` | Store、Coordinator 和初始 Prompt 装配 |
| 修改 | `.gitignore` | 忽略项目自动记忆 |
| 修改 | `README.md`、`pyproject.toml`、`src/kcode/__init__.py`、`uv.lock` | 用户文档和 0.7.0 版本 |
| 新建/修改 | `tests/test_memory_*.py`、相关现有测试 | 单元、集成、TUI 和端到端覆盖 |

## T0：文档门槛与一期基线

**文件：** `docs/long-term-memory/`、一期现有变更
**依赖：** 无

**步骤：**

1. 写入已批准的四份二期文档。
2. 扫描旧项目命名、旧目录前缀、未完成内容和不存在的接口。
3. 向用户提供四个磁盘文件，等待文件级确认。
4. 确认 `hello.txt` 是用户文件并始终排除。
5. 完整运行一期 compileall、ruff 和 pytest。
6. 检查一期 diff 后作为独立 `0.6.0` 提交。
7. 创建 `feature/session-memory` 分支，二期从该提交继续。

**验证：** 四文档路径和内容正确；一期全部检查通过；一期提交不含 `hello.txt` 或二期实现。

## T1：配置、模型与安全常量

**文件：** `src/kcode/config.py`、`src/kcode/memory/models.py`、`config.example.yaml`
**依赖：** T0

**步骤：**

1. 新增严格的 `MemoryConfig(enabled=False)` 并接入 AppConfig。
2. 在配置合并阶段记录来源，允许项目关闭但禁止项目开启未获同意的记忆功能。
3. 定义 MemoryType、MemoryScope、MemoryStatus、MemoryAction。
4. 定义 MemoryRecord、MemoryProposal、CompletedTurn、MemoryDecision、MemoryState 和结果模型。
5. 固定字段长度、每轮最多三条候选、24 KiB/200 行预算、治理阈值和关闭超时。
6. 在示例配置中说明费用、明文存储和关闭方式。

**验证：** 运行配置与模型测试，确认默认关闭、用户启用、项目降权、未知字段和非法组合均符合设计。

## T2：安全路径与双作用域目录

**文件：** `src/kcode/memory/paths.py`、`tests/test_memory_store.py`
**依赖：** T1

**步骤：**

1. 解析 `~/.kcode/memory/` 与 `<workspace>/.kcode/memory/`。
2. 校验 workspace 和目标根目录的解析边界。
3. 为 entries、proposals、索引、状态、锁和临时文件提供集中路径函数。
4. 拒绝根目录、目标文件和锁路径的符号链接。
5. 只在记忆功能启用并实际加载时创建目录。

**验证：** 运行路径测试，覆盖用户/项目隔离、越界、相对路径、symlink 和未启用不创建目录。

## T3：记录和候选编解码

**文件：** `src/kcode/memory/store.py`、`tests/test_memory_store.py`
**依赖：** T1、T2

**步骤：**

1. 实现 YAML frontmatter + Markdown 正文的严格编码和解析。
2. 实现候选与状态的规范化 JSON 编码和解析。
3. 使用 Pydantic 拒绝未知字段、错误 schema、非法枚举、超长字段和跨字段冲突。
4. 生成稳定记录 ID 和确定性候选 ID。
5. 将坏 UTF-8、坏 YAML/JSON 和非法文件转换为独立 warning。

**验证：** 运行 store round-trip 测试，确认 Unicode、换行、非法输入和确定性 ID 行为。

## T4：锁、原子写与 Store 操作

**文件：** `src/kcode/memory/store.py`、`tests/test_memory_store.py`
**依赖：** T3

**步骤：**

1. 为每个 scope 增加 `filelock` 跨进程锁。
2. 实现同目录临时文件、flush、fsync、权限收紧和 `os.replace`。
3. 实现 load、save、save_proposal、pending、set_status、delete 和 resolve_proposal。
4. 实现最近 500 个候选决策哈希和 session 计数状态。
5. 保证 create/update/status/resolve 在锁内重新读取并校验目标。
6. 清理安全失败产生的临时文件，不覆盖并发更新。

**验证：** 运行权限、锁竞争、写入故障、并发更新、崩溃残留和不存在目标测试。

## T5：索引与 Prompt 渲染

**文件：** `src/kcode/memory/prompting.py`、`src/kcode/memory/store.py`、`tests/test_memory_prompting.py`
**依赖：** T4

**步骤：**

1. 从 entries 重建每个 scope 的 `MEMORY.md`。
2. 只渲染 active 记录的类型、标题、摘要、应用方式和 ID。
3. 实现项目级冲突优先和两个 scope 的最低预算。
4. 在 24 KiB/200 行内确定性选择记录，并返回排除数量。
5. 索引缺失、被手改或校验失败时从记录重建。
6. 超预算只产生 warning，不修改或删除记录。

**验证：** 运行索引重建、状态过滤、优先级、双 scope 不饿死、UTF-8 字节预算和确定性截断测试。

## T6：本地信号检测

**文件：** `src/kcode/memory/signals.py`、`tests/test_memory_signals.py`
**依赖：** T1

**步骤：**

1. 定义明确记忆、偏好、纠正、稳定决定和参考资料的中英文信号。
2. 规范化大小写与空白，但保留来源原文用于后续提取。
3. 排除普通问答、一次性临时请求和失败轮次。
4. 返回命中种类和可观测理由，便于测试和诊断。

**验证：** 运行中英文正例、反例、混合语言、临时请求和边界文本测试。

## T7：结构化提取与秘密校验

**文件：** `src/kcode/memory/extraction.py`、`tests/test_memory_extraction.py`
**依赖：** T1、T5、T6

**步骤：**

1. 使用当前 ChatProvider、稳定系统消息和用户消息发起无工具流。
2. 只组装本轮用户文本、最终回答和活跃精炼索引。
3. 收集流式文本并要求单个纯 JSON 文档。
4. 严格解析最多三条候选，校验作用域、动作和更新目标。
5. 在请求前替换已知 sensitive values，在落盘前再次脱敏。
6. 检测常见 API key、私钥和凭据赋值形态；核心字段含秘密或 `[REDACTED]` 时拒绝整条候选。
7. Provider、流、JSON 或校验失败转换为记忆 warning，不抛入主聊天。

**验证：** 运行 Provider 流、非法 JSON、额外文本、未知目标、候选上限、秘密、取消和调用负载测试，确认不发送工具结果或 thinking。

## T8：MemoryCoordinator 与后台队列

**文件：** `src/kcode/memory/runtime.py`、`src/kcode/memory/__init__.py`、`tests/test_memory_runtime.py`
**依赖：** T4、T5、T6、T7

**步骤：**

1. 加载两个 Store，恢复待审候选并生成初始 Prompt。
2. `submit_turn` 同步检测信号，命中后向单一后台队列提交不可变轮次。
3. 提取完成后校验、去重并先落盘候选，再发送通知。
4. 实现确认、编辑确认、拒绝、失效、恢复和永久删除的统一入口。
5. 每次状态变更后重建索引并返回新 Prompt 和 warning。
6. 实现 scope 独立降级、错误节流、敏感值更新和有界关闭。

**验证：** 运行重启恢复、重复候选、队列顺序、写入失败、单 scope 降级、取消和关闭无半写测试。

## T9：Agent 与 Prompt 接入

**文件：** `src/kcode/orchestration.py`、`src/kcode/prompting/`、`tests/test_agent_loop.py`
**依赖：** T8

**步骤：**

1. 为 AgentRunner 增加 MemoryCoordinator 绑定。
2. 只在最终回答提交且 checkpoint 已尝试后构造 CompletedTurn。
3. 保证取消、流错误、空响应、迭代上限和未知工具停止不提交。
4. 提交后台任务时不等待模型提取，不延迟完成事件。
5. 增加空闲时 `long_term_memory` 稳定 Prompt 刷新；忙碌时拒绝替换。
6. 确认后台调用不写 Conversation 或会话 JSONL。

**验证：** 运行全部停止原因、checkpoint 降级、Prompt 忙碌保护、完成延迟和 JSONL 无后台消息测试。

## T10：Session 生命周期与治理计数

**文件：** `src/kcode/history/runtime.py`、`src/kcode/memory/runtime.py`、`tests/test_history_runtime.py`
**依赖：** T8、T9

**步骤：**

1. 定义通用可选 SessionCloseListener。
2. 在 clear、resume 和 exit 关闭旧 session 后通知监听者。
3. 把监听异常转换为 warning，不阻断会话切换。
4. Coordinator 只记录包含成功轮次且尚未计数的 session ID。
5. 保证切换中的提取保持原来源 session ID。

**验证：** 运行 clear/resume/exit、空 session、重复关闭、监听异常和旧来源归属测试。

## T11：治理调度与建议

**文件：** `src/kcode/memory/governance.py`、`src/kcode/memory/runtime.py`、`tests/test_memory_governance.py`
**依赖：** T8、T10

**步骤：**

1. 用户级和项目级分别检查十条 active、二十四小时和五个完成 session 条件。
2. 只有三个条件同时满足且不在运行/节流期时才排治理任务。
3. 只向 Governor 发送单 scope 活跃精炼记录。
4. 仅接受 update、merge、inactivate 建议，拒绝 delete、跨 scope 和无目标动作。
5. 成功后更新时间并清计数，失败保留计数并节流。
6. 将建议保存为普通候选，复用确认入口。

**验证：** 运行阈值边界、scope 隔离、重复排队、失败重试、非法动作和确认前零修改测试。

## T12：TUI 审核弹窗

**文件：** `src/kcode/ui/memory.py`、`src/kcode/ui/app.py`、`tests/test_memory_ui.py`
**依赖：** T8、T9

**步骤：**

1. 实现 MemoryReviewScreen，显示类型、scope、理由、来源和 create/update diff。
2. 支持确认、编辑确认、拒绝和 Escape 稍后处理。
3. 将编辑内容作为决策返回 Coordinator 重新校验。
4. 只在生成结束、界面空闲且没有其他 Modal 时依次自动审核。
5. 重启后按持久化时间恢复审核顺序。

**验证：** 运行键盘导航、取消、编辑、diff、自动弹出顺序、Modal 冲突和重启待审测试。

## T13：Ctrl+M 记忆面板

**文件：** `src/kcode/ui/memory.py`、`src/kcode/ui/app.py`、`tests/test_memory_ui.py`
**依赖：** T12

**步骤：**

1. 注册 `Ctrl+M`，不增加 `/memory` 命令。
2. 分组显示待审、active、inactive、治理建议和 warnings。
3. 提供审核、失效、恢复和永久删除动作。
4. 默认删除操作映射为 inactive；永久删除使用独立二次确认。
5. 生成或授权期间按键只显示稍后打开提示。
6. 状态变更后刷新 Prompt 和面板，不替换 Conversation。

**验证：** 运行快捷键、列表分组、失效/恢复、二次确认、生成中保护和聊天重绘测试。

## T14：CLI 装配与端到端

**文件：** `src/kcode/cli.py`、`.gitignore`、`tests/test_memory_runtime.py`
**依赖：** T9、T10、T11、T13

**步骤：**

1. 配置关闭时保持原装配路径，不创建记忆目录。
2. 配置开启时构造双 Store、MemoryCoordinator 和初始 Prompt。
3. 绑定 Runner、App 和 SessionCoordinator 监听。
4. 将 MCP 新发现的 sensitive values 同步到记忆层。
5. 忽略 `.kcode/memory/`，不影响已有 session 和本地配置忽略规则。
6. 使用 FakeProvider 实现完整跨重启流程测试。

**验证：** 运行“启用 → 对话 → 候选 → 重启 → 审核 → 新会话注入”、更新旧记忆、项目覆盖用户偏好和 Provider 失败仍可聊天测试。

## T15：用户文档、版本和最终验收

**文件：** `README.md`、`config.example.yaml`、`pyproject.toml`、`src/kcode/__init__.py`、`uv.lock`、`docs/long-term-memory/checklist.md`
**依赖：** T14

**步骤：**

1. 文档说明长期记忆、会话恢复、KCODE.md 和上下文摘要的区别。
2. 说明启用、费用、明文风险、Ctrl+M、失效/恢复和永久删除。
3. 将版本与锁文件升级至 `0.7.0`。
4. 扫描旧项目命名、旧接口和未完成实现内容。
5. 运行 checklist 的每项验证并记录真实证据。

**验证：** compileall、ruff、完整 pytest 和 `git diff --check` 全部通过，checklist 使用真实结果更新。

## T16：提交与上传

**文件：** Git 暂存区与提交历史
**依赖：** T15

**步骤：**

1. 检查 diff、未跟踪文件和一期/二期提交边界。
2. 确认 `hello.txt`、本地配置、session、memory 和秘密均未暂存。
3. 将二期作为独立提交，不重写一期提交。
4. 推送 `feature/session-memory` 到 origin，不推 main、不强推。
5. 回报两个提交 ID、远端分支、测试结果和仍未提交的用户文件。

**验证：** `git status`、`git diff --cached`、`git log --oneline` 和远端分支检查均符合上述边界。

## 执行顺序

```text
T0 → T1 → T2 → T3 → T4 → T5
          └──────→ T6 → T7 ─┐
                              ▼
                             T8 → T9 → T10 → T11
                                       └──────→ T12 → T13
                                                      ▼
                                                     T14 → T15 → T16
```
