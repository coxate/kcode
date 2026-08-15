# KCode 项目指令与会话持久化 Plan

## 架构概览

本期新增两个独立子系统：项目指令加载和会话持久化。指令加载器只在启动时构建稳定 Prompt；会话协调器把同一 session ID 下的完整对话、上下文管理器、日志和租约绑定为一个可切换的运行时。

```text
启动
 ├─ InstructionLoader → InstructionBundle → custom_instructions
 └─ SessionCoordinator → fresh SessionRuntime
                              ├─ Conversation
                              ├─ ContextManager
                              ├─ SessionJournal
                              └─ SessionLease

用户轮次
 AgentRunner
   → 提交 canonical Conversation
   → 把新增消息作为检查点交给 SessionJournal
   → 工作线程 append + flush + fsync
   → 返回成功或可见的持久化警告

/resume
 SessionStore.list_sessions()
   → ResumeScreen 选择
   → 获取 SessionLease
   → SessionStore.load() 解析和修复内存视图
   → SessionCoordinator 切换 SessionRuntime
   → 必要时 ContextManager 压缩
   → 重绘聊天轮次
```

### 责任边界

- `Conversation` 仍是进程内 canonical history 的唯一所有者。
- `ContextManager` 仍只构造模型视图，不改写原始历史。
- `SessionJournal` 只接受已提交的消息批次，不监听流式 Token。
- `SessionCoordinator` 是切换会话的唯一入口；`/clear` 和 `/resume` 不直接替换零散对象。
- `AgentRunner` 在已有异步检查点调用 Journal，不向同步 `Conversation` 注入异步回调。
- Provider 适配层继续负责把内部消息转为 Anthropic/OpenAI 线格式。

## 核心数据结构与接口

### 指令子系统

- `InstructionSource(level, path, boundary)`：表示用户、项目、本机项目来源和允许 include 的根边界。
- `InstructionWarning(code, path, detail)`：交给启动 UI 显示的结构化警告，不混入 Prompt。
- `InstructionBundle(content, warnings, loaded_paths, truncated)`：已按优先级和 32 KiB 预算组装的结果。
- `InstructionLoader.load(project_root, user_home) -> InstructionBundle`：纯文件加载边界。

### JSONL schema v1

```json
{"type":"session","schema":1,"session_id":"20260808-103000-a1b2","created_at":1786156200,"provider":"anthropic","model":"..."}
{"type":"message","ts":1786156201,"message":{"kind":"user","content":"..."}}
{"type":"message","ts":1786156202,"message":{"kind":"assistant","content":"","tool_calls":[{"index":0,"id":"call_1","name":"read_file","arguments_json":"{\"path\":\"README.md\"}"}]}}
{"type":"message","ts":1786156203,"message":{"kind":"tool_result","tool_call_id":"call_1","tool_name":"read_file","result":{"status":"success","data":{"content":"..."},"duration_ms":3,"truncated":false}}}
{"type":"session_end","ts":1786156300,"reason":"exit"}
```

- `session` 必须是首条记录，用于版本检查和列表元数据。
- `message` 只支持 `user`、`assistant`、`tool_result` 三种内部类型。
- `assistant.tool_calls` 固定保存 `index`、`id`、`name` 和原始 `arguments_json`；`tool_result.result` 使用 KCode 的稳定 `ToolResult.to_dict()` 结构。schema v1 拒绝未知字段，不序列化 Python 类名。
- `session_end` 表示某次 runtime 的正常关闭边界，不代表文件永久结束。会话恢复续写后可以出现后续消息和新的 `session_end`；若最后一段缺少该记录，则提示“上次可能异常退出”，但不伪造丢失内容。
- 不保存稳定 System Prompt、环境快照、临时提醒、摘要或 Provider continuation。

### 会话子系统

- `SessionMetadata`：schema、ID、创建时间、原 Provider 和原模型。
- `SessionSummary`：列表所需 ID、标题、最后活跃时间、模型、大小、消息数和占用状态。
- `LoadedSession`：元数据、修复后的内存消息、可展示轮次、恢复警告和最后活跃时间。
- `PersistenceState`：`healthy | degraded | closed`，保存首个失败原因。
- `SessionJournal.append_checkpoint(messages)`：有序追加一个已提交批次。
- `SessionJournal.close(reason)`：写入正常结束记录并释放资源。
- `SessionStore.list_sessions()`：有界扫描有效新格式会话。
- `SessionStore.load(session_id)`：完整解析和修复恢复内容。
- `SessionLease`：封装 `filelock.FileLock` 的非阻塞跨进程租约。
- `SessionRuntime`：把同 ID 的 `Conversation`、`ContextManager`、`SessionJournal` 和一次性恢复提醒绑定。
- `SessionCoordinator.new_session()`、`resume(session_id)`、`clear()`、`close()`：统一管理会话生命周期。

### 现有接口的最小变化

- `Conversation.restore(messages)`：导入已校验消息，重建消息和可展示轮次。
- `AgentRunner.bind_session(runtime)`：仅允许在空闲时替换 Conversation、ContextManager 和 Journal。
- `SystemPromptBuilder.with_content(name, content)`：返回新 Builder，不改写全局默认 section。
- `ResumeScreen`：返回选中的 session ID 或 `None`；不承担业务加载。

## 模块设计

### `kcode.instructions`

**职责：** 发现三层指令、安全展开 include、执行预算和构建来源标签。

**依赖：** 只依赖标准库文件系统。

**边界：** 用户级文件只能 include `~/.kcode/` 内的文件；项目级和本机项目级文件只能 include `project_root` 内的文件。32 KiB 按 UTF-8 编码后的展开字节数计算。

### `kcode.history.ids` 与 `models`

**职责：** 生成/校验 session ID，定义内存数据结构和严格磁盘 schema。

### `kcode.history.codec`

**职责：** 在稳定磁盘 payload 和 KCode 内部消息之间双向转换，显式拒绝不应持久化的消息。

### `kcode.history.journal`

**职责：** 惰性创建会话存储、获取租约、追加检查点、刷盘和关闭。

**并发：** 每个 Journal 使用一个专用单工作线程；锁获取、文件句柄操作和释放均位于同一线程。

### `kcode.history.store`

**职责：** 列出会话、有界读取列表元数据、完整解析日志和修复工具链。

### `kcode.history.runtime`

**职责：** 构造新 runtime、准备恢复候选、只在候选完整就绪后切换、处理 clear 和 close。

### `kcode.ui.resume`

**职责：** 展示内存中的 SessionSummary，执行搜索过滤和键盘选择。

## 模块交互

### 启动

1. CLI 解析现有配置、权限和 Provider。
2. `InstructionLoader` 读取三层指令并生成 Bundle。
3. 生成 fresh session ID，以同一 ID 创建 ContextManager 和惰性 Journal。
4. 把指令写入新 Prompt Builder 实例，警告交给现有 startup warnings。
5. 启动 TUI；在首个成功检查点前不创建 session 目录。

### Agent 轮次

1. Runner 在修改 Conversation 前记录消息长度。
2. 工具批次完成后调用现有 checkpoint，取新增切片并等待 Journal 完成。
3. 最终回答完成后同样持久化新增切片。
4. 写入失败时 runtime 进入 degraded，Runner 产生可见通知。

### `/resume` 安全切换

1. 当前 runtime 保持不动，后台构造候选会话。
2. 非阻塞获取目标租约；失败则显示占用。
3. 校验 header 并逐行解析；坏行计数，悬空调用在内存补状态未知结果，孤立结果丢弃。
4. 重建 Conversation 和使用原 session ID 的 ContextManager。
5. 预算超限时先生成压缩视图；失败则放弃候选，当前 runtime 仍可用。
6. 候选完全就绪后，关闭旧 session 并切换 runtime。
7. 重绘历史并显示警告。重绘前先构造可展示数据；如纯 UI 挂载失败，恢复后的会话状态仍保持有效并显示错误。

### `/clear` 与退出

- `/clear` 先尝试正常关闭旧 runtime，再创建 fresh runtime；关闭失败只产生警告，不删除数据。
- 正常退出先停止生成，再关闭 Journal 并释放租约，最后关闭 MCP。
- 异常退出由 OS 锁释放；缺少末尾 `session_end` 时下次恢复显示提示。

### 会话列表

- 在工作线程扫描 `<project>/.kcode/sessions/*/conversation.jsonl`。
- 列表阶段只有界读取 header、首个用户记录和 stat，不全量解析大日志。
- session ID、header ID 与目录名必须一致；不一致、旧格式或未知 schema 不展示。
- Coordinator 从候选中排除当前 runtime 的 session ID，避免同进程重新争抢自己的租约。
- ResumeScreen 在内存中过滤 Summary；真正续写前再次获取租约。

## 文件组织

```text
kcode/
├─ docs/session-memory/
│  ├─ spec.md
│  ├─ plan.md
│  ├─ task.md
│  └─ checklist.md
├─ src/kcode/
│  ├─ instructions.py
│  ├─ history/
│  │  ├─ __init__.py
│  │  ├─ ids.py
│  │  ├─ models.py
│  │  ├─ codec.py
│  │  ├─ journal.py
│  │  ├─ store.py
│  │  └─ runtime.py
│  └─ ui/resume.py
└─ tests/
   ├─ test_instructions.py
   ├─ test_history_codec.py
   ├─ test_history_journal.py
   ├─ test_history_store.py
   ├─ test_history_runtime.py
   ├─ test_session_persistence_integration.py
   └─ test_resume_ui.py
```

必要集成修改集中在 `conversation.py`、`context/manager.py`、`orchestration.py`、`prompting/builder.py`、`cli.py`、`ui/commands.py` 和 `ui/app.py`；现有 Provider、工具和权限模块不迁移。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 版本 | `0.5.0 → 0.6.0` | 向后兼容的新用户能力 |
| 跨进程锁 | `filelock>=3.20,<4` | 成熟跨平台 OS 文件锁，避免维护 Unix/Windows 双实现 |
| schema | 带版本的严格 JSONL | 可演进，不把 Python 对象内部布局当持久化协议 |
| 时间 | ID 用本地时间，记录用 Unix 秒 | ID 易读，记录易排序且跨时区 |
| 随机后缀 | 安全随机 2 bytes 转 4 位十六进制 | 防同秒碰撞 |
| 规范记录 | 保留完整原始历史 | 摘要失败或遗漏时仍可恢复 |
| 模型视图 | ContextManager 独立维护 | 不污染 Conversation 和 JSONL |
| 刷盘 | 检查点级 flush + fsync | 保留已提交工具轨迹，不为每个 Token 写盘 |
| 线程 | Journal 专属单工作线程 | 保证锁与文件句柄在同一线程中按序使用 |
| 权限 | POSIX 目录 `0700`、文件 `0600` | 降低本机泄漏面；Windows 使用系统 ACL 默认并提示局限 |
| Git 防护 | sessions 根目录内部 `.gitignore` 写 `*` | 默认忽略日志且不改用户项目根文件 |
| Prompt | 三层低到高排列 + 显式冲突规则 | 不仅依赖不稳定的 Prompt 位置偏好 |
| 恢复模型 | 当前 Provider/模型 | 保持厂商无关并避免新增运行时切模型 |
| 恢复提醒 | 首个 Agent 轮次一次性 SystemReminder | 提醒重新核实，不污染原历史 |
| 迁移 | 不改旧目录和旧 Artifact | 避免误删或错误猜测旧格式 |
| 删除 | 不实现 | 本期无任何自动数据删除权限 |

## 测试设计

1. 指令单元测试：三层顺序、冲突标签、include 安全和 32 KiB 预算。
2. Codec/恢复单元测试：三种消息 round-trip、严格 schema、坏行和工具链修复。
3. Journal/租约测试：顺序、fsync 故障、权限、Git ignore 和多进程锁。
4. Context/Runner 集成测试：检查点、恢复压缩和一次性提醒。
5. Textual 测试：搜索、选择、取消、恢复重绘和 `/clear`。
6. 端到端测试：FakeProvider 完成含工具会话、退出、再启动恢复、继续和 clear。
7. 回归：`python -m compileall src`、`ruff check`、完整 `pytest`；不联网、不读真实密钥、不产生费用。
