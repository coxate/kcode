# KCode 项目指令与会话持久化 Tasks

## 文件清单

### 新增

- `docs/session-memory/spec.md`、`plan.md`、`task.md`、`checklist.md`：需求、设计、任务和验收门槛。
- `src/kcode/instructions.py`：三层指令和安全 include。
- `src/kcode/history/__init__.py`、`ids.py`、`models.py`、`codec.py`、`journal.py`、`store.py`、`runtime.py`：会话持久化子系统。
- `src/kcode/ui/resume.py`：会话选择界面。
- `tests/test_instructions.py`、`test_history_codec.py`、`test_history_journal.py`、`test_history_store.py`、`test_history_runtime.py`、`test_session_persistence_integration.py`、`test_resume_ui.py`：新增行为验证。

### 修改

- `pyproject.toml`、`uv.lock`、`README.md`、`.gitignore`：版本、依赖、用法和项目默认忽略。
- `src/kcode/conversation.py`、`context/manager.py`、`orchestration.py`：恢复、统一 session ID 和检查点。
- `src/kcode/prompting/builder.py`：不可变 Prompt section 内容替换。
- `src/kcode/cli.py`、`ui/commands.py`、`ui/app.py`：启动、`/resume`、`/clear` 和退出生命周期。
- 必要的现有测试：保持旧公开行为并加入新依赖。

用户已有的未跟踪文件 `hello.txt` 不修改。

## T1：落盘四份批准文档

**文件：** `docs/session-memory/`
**依赖：** 无

**步骤：**
1. 创建四份文档。
2. 写入已批准内容。
3. 扫描其他项目命名和未完成标记。

**验证：** 使用由分段字符串拼成的正则，扫描其他项目命名、旧目录名和未完成标记；结果必须无匹配。分段写法用于避免验证规则在本文档中自匹配。

## T2：增加依赖与版本

**文件：** `pyproject.toml`、`uv.lock`
**依赖：** T1

**步骤：**
1. 版本改为 0.6.0。
2. 加入 `filelock>=3.20,<4`。
3. 更新锁文件。

**验证：** `uv sync --extra dev` 成功，`uv run python -c "import filelock"` 成功。

## T3：实现 session ID

**文件：** `src/kcode/history/ids.py`、`src/kcode/context/manager.py`
**依赖：** T2

**步骤：**
1. 实现新 ID 生成、严格解析和安全路径校验。
2. ContextManager 改用新生成器。
3. 保留显式 session ID 注入能力。

**验证：** ID 格式、同秒随机性、非法字符和旧格式测试通过。

## T4：定义 history models

**文件：** `src/kcode/history/models.py`
**依赖：** T3

**步骤：**
1. 定义 metadata、summary、loaded session 和 persistence state。
2. 定义 schema v1 记录模型。
3. 禁止未知额外字段。

**验证：** 合法构造成功，错误 type/schema/字段被拒绝。

## T5：实现消息 codec

**文件：** `src/kcode/history/codec.py`、`tests/test_history_codec.py`
**依赖：** T4

**步骤：**
1. 实现三种内部消息与磁盘 payload 双向转换。
2. 明确排除 continuation、system、environment 和 reminder。
3. 覆盖中文、换行、多工具和错误结果。

**验证：** `uv run pytest tests/test_history_codec.py -q` 通过。

## T6：实现指令来源扫描

**文件：** `src/kcode/instructions.py`、`tests/test_instructions.py`
**依赖：** T2

**步骤：**
1. 定义 Source、Warning 和 Bundle。
2. 按低到高顺序发现三层文件。
3. 对缺失顶层文件静默跳过。

**验证：** 三层存在、部分缺失和全缺失测试通过。

## T7：实现安全 include

**文件：** `src/kcode/instructions.py`、`tests/test_instructions.py`
**依赖：** T6

**步骤：**
1. 解析独占行语法。
2. 解析相对路径和符号链接。
3. 执行边界、深度和当前链环路检查。

**验证：** 普通嵌套、重复非环路、真环路、六层、`..`、绝对路径和边界外 symlink 测试通过。

## T8：实现指令预算与错误隔离

**文件：** `src/kcode/instructions.py`、`tests/test_instructions.py`
**依赖：** T7

**步骤：**
1. 检测 UTF-8 和二进制内容。
2. 按剩余预算有界读取，在完整内容边界停止。
3. 构建来源标签和冲突说明。

**验证：** 32 KiB、坏编码、不可读和单项失败不中断后续文件的测试通过。

## T9：实现 SessionLease

**文件：** `src/kcode/history/journal.py`、`tests/test_history_journal.py`
**依赖：** T2、T3

**步骤：**
1. 封装非阻塞 FileLock。
2. 严格限定锁路径。
3. 定义 busy 错误和幂等释放。

**验证：** 同进程争抢、两个子进程争抢和持锁进程退出后重获测试通过。

## T10：实现 Journal 首次创建

**文件：** `src/kcode/history/journal.py`、`tests/test_history_journal.py`
**依赖：** T4、T5、T9

**步骤：**
1. 惰性创建权限收紧目录和内部 ignore 文件。
2. 获取租约并安全创建 JSONL。
3. 把 header 和首批消息作为一次刷盘。

**验证：** 空 session 无目录；首次 checkpoint 后结构、权限、Git ignore 和一次 fsync 正确。

## T11：实现 Journal 后续检查点与关闭

**文件：** `src/kcode/history/journal.py`、`tests/test_history_journal.py`
**依赖：** T10

**步骤：**
1. 用单线程 executor 顺序追加。
2. 实现 healthy/degraded/closed 状态机。
3. 关闭时追加 `session_end` 并释放资源。

**验证：** 并发提交仍有序、慢 fsync 不阻塞事件循环、失败进入 degraded、close 幂等。

## T12：实现轻量会话列表

**文件：** `src/kcode/history/store.py`、`tests/test_history_store.py`
**依赖：** T3、T4、T9

**步骤：**
1. 扫描新格式目录。
2. 有界读取 header 和首个 user。
3. 结合 stat 构建 Summary 并检测 busy。

**验证：** 排序、标题截断、模型、大小、排除当前 runtime、旧目录、未知 schema 和身份不一致测试通过。

## T13：实现完整加载与修复

**文件：** `src/kcode/history/store.py`、`tests/test_history_store.py`
**依赖：** T5、T12

**步骤：**
1. 逐行解析并统计坏行。
2. 恢复消息，补缺失工具结果并丢弃孤立结果。
3. 生成异常退出和跨模型警告。

**验证：** 坏中间行、半行结尾、多工具缺部分结果、孤立结果和日志字节不变测试通过。

## T14：实现 Conversation.restore

**文件：** `src/kcode/conversation.py`、`tests/test_conversation.py`
**依赖：** T13

**步骤：**
1. 只接受已校验消息。
2. 重建 `_messages`、聊天轮次和下一 turn ID。
3. 拒绝覆盖活跃轮次。

**验证：** 纯文本、工具链、未完成尾部和恢复后开始新 turn 测试通过。

## T15：实现新会话 runtime 与 coordinator

**文件：** `src/kcode/history/runtime.py`、`tests/test_history_runtime.py`
**依赖：** T11、T14

**步骤：**
1. 绑定同 ID 的 Conversation、ContextManager 和 Journal。
2. 实现 fresh、close 和 clear。
3. 向上层返回关闭警告。

**验证：** 共享 ID、clear 换 ID、旧日志停止增长和关闭失败警告测试通过。

## T16：实现候选恢复与安全切换

**文件：** `src/kcode/history/runtime.py`、`tests/test_history_runtime.py`
**依赖：** T15

**步骤：**
1. 先获取和加载候选。
2. 构造 ContextManager，必要时执行预压缩。
3. 准备可展示轮次，成功后再关闭旧 runtime 并切换。

**验证：** busy、加载失败和压缩失败均保留旧 runtime；成功后续写原日志。

## T17：接入动态 Prompt

**文件：** `src/kcode/prompting/builder.py`、`src/kcode/cli.py`、相关测试
**依赖：** T8、T15

**步骤：**
1. Builder 支持不可变 section 内容替换。
2. CLI 加载 InstructionBundle。
3. App/Runner 使用定制 stable prompt，警告复用 startup warnings。

**验证：** 空指令保持原 Prompt，三层指令位于 custom slot，cache 前缀在进程内稳定。

## T18：接入 AgentRunner 检查点

**文件：** `src/kcode/orchestration.py`、`tests/test_session_persistence_integration.py`
**依赖：** T15、T16

**步骤：**
1. Runner 支持空闲时绑定 runtime。
2. 在工具 checkpoint 和 final commit 后持久化新增消息切片。
3. 持久化失败时产生持续可见警告。

**验证：** 无工具一次批次、多工具批次、取消/Provider 错误和 Journal 失败测试通过。

## T19：接入一次性恢复提醒

**文件：** `src/kcode/conversation.py`、`src/kcode/orchestration.py`、集成测试
**依赖：** T18

**步骤：**
1. 扩展 reminder kind。
2. 恢复首轮的每个内部迭代都携带提醒。
3. 首轮结束后消费，不写入 Conversation 或 JSONL。

**验证：** 工具多迭代首轮均有提醒，第二个用户轮次没有，JSONL 没有提醒。

## T20：实现 ResumeScreen

**文件：** `src/kcode/ui/resume.py`、`tests/test_resume_ui.py`
**依赖：** T12

**步骤：**
1. 组合 OptionList 和搜索 Input。
2. 实现上下键、Enter 和 Esc。
3. 显示 busy 和无会话状态。

**验证：** Textual pilot 覆盖过滤、选择、取消和焦点。

## T21：接入 `/resume`

**文件：** `src/kcode/ui/commands.py`、`src/kcode/ui/app.py`、`tests/test_resume_ui.py`
**依赖：** T16、T18、T20

**步骤：**
1. 增加命令解析和帮助。
2. 空闲时打开 Screen 并显示加载状态。
3. 成功切换后重绘用户/助手轮次和工具状态摘要。

**验证：** 命令不进 Provider，生成中拒绝，成功/失败提示正确。

## T22：更新 `/clear` 与退出生命周期

**文件：** `src/kcode/ui/app.py`、现有 App 测试
**依赖：** T21

**步骤：**
1. clear 统一走 Coordinator。
2. unmount 等待 Journal 后再关闭 MCP。
3. 避免重复 close。

**验证：** clear、exit 和取消生成后退出的顺序测试通过。

## T23：隐私与兼容回归

**文件：** `src/kcode/history/`、`README.md`、`.gitignore`、相关测试
**依赖：** T22

**步骤：**
1. 日志写入前沿用 sensitive values 脱敏。
2. README 说明明文风险、`KCODE.md` 不得放秘密、旧目录策略。
3. 验证旧 Artifact 和无新文件启动行为。

**验证：** 已知 Key 不出现在 JSONL，旧 Artifact 测试和启动测试通过。

## T24：完整质量检查

**文件：** 全部变更
**依赖：** T23

**步骤：**
1. 运行 compileall。
2. 运行 ruff。
3. 运行完整 pytest 并修复回归。

**验证：** 三条命令全部退出码 0，无网络调用。

## T25：端到端验收

**文件：** `tests/test_session_persistence_integration.py`、`docs/session-memory/checklist.md`
**依赖：** T24

**步骤：**
1. FakeProvider 完成含工具会话并退出。
2. 新 App 搜索恢复、继续对话并 clear。
3. 按 Checklist 逐项记录实际证据。

**验证：** AC1–AC24 全部有通过证据；任何失败修复后重跑。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5
                 ├→ T6 → T7 → T8
                 └→ T9 → T10 → T11 → T12 → T13 → T14
T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21 → T22 → T23 → T24 → T25
```
