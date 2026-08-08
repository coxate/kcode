# KCode 长期记忆二期 Checklist

> 每项必须通过运行代码或观察行为验证。实施前保持未勾选；完成后记录真实命令、结果和测试数量。

## A. 文档与边界

- [x] 四份长期记忆文档位于 `docs/long-term-memory/` 且与审批内容一致。（验证：逐文件 review 与 Git diff）
- [x] 全仓没有旧项目命名、旧目录前缀、旧接口或未完成实现内容。（验证：`rg` 定向扫描）
- [x] 二期没有 RAG、Embedding、云同步、加密承诺、自动批准、自动更新或自动删除。（验证：文档 review 与功能测试）
- [x] 没有新增 `/memory` 等 slash command，管理入口为 `Ctrl+M`。（验证：命令解析测试与 TUI 测试）
- [x] `KCODE.md`、会话 JSONL、上下文摘要和长期记忆的职责明确区分。（验证：README review）

## B. 配置与费用保护

- [x] 未配置时 `memory.enabled` 为关闭，不额外调用模型或创建记忆目录。（验证：配置和 CLI 集成测试）
- [x] 用户级配置可以显式开启，项目级配置不能从关闭提升为开启。（验证：配置来源测试）
- [x] 示例配置说明后台调用费用、本地明文、关闭方式和秘密识别边界。（验证：配置文档 review）
- [x] 提取与治理复用当前 Provider/模型，不保存厂商专属状态或新增凭据。（验证：Provider 集成测试与 schema review）

## C. 存储、安全与可恢复性

- [x] 用户级和项目级目录、记录、候选、索引和状态符合 schema 1。（验证：Store round-trip 测试）
- [x] Markdown frontmatter、正文和 JSON 候选严格 round-trip，非法字段与组合被拒绝。（验证：编解码测试）
- [x] POSIX 下目录与文件权限收紧为 `0700/0600`。（验证：权限测试）
- [x] 路径越界、symlink、坏 UTF-8、坏 YAML/JSON 和不存在目标安全失败。（验证：安全边界测试）
- [x] 所有修改受 `filelock` 保护并使用 flush、fsync、同目录临时文件和原子替换。（验证：锁与故障注入测试）
- [x] 单文件损坏不阻塞其他记忆，索引可从 entries 重建。（验证：坏文件隔离测试）
- [x] 项目 `.kcode/memory/` 被 Git 忽略，用户目录和项目目录不会误提交。（验证：`git check-ignore` 与暂存区检查）
- [x] 已知敏感值被脱敏，常见密钥/私钥/凭据形态被阻止，含 `[REDACTED]` 的核心候选不落盘。（验证：秘密测试）
- [x] 默认清理只标为 inactive 且可恢复，永久删除必须由用户二次确认。（验证：Store 与 TUI 测试）
- [x] 模型动作类型不存在永久 delete，治理路径无法物理删除记录。（验证：模型与治理测试）

## D. 提取与候选审核

- [x] 只有成功完成并提交的回答生成 CompletedTurn，所有异常停止路径均不会。（验证：Agent 集成测试）
- [x] 未命中本地信号时 Provider 调用次数不增加。（验证：FakeProvider 调用计数）
- [x] 命中时只发送本轮用户文本、最终回答和精炼索引，不发送工具结果、thinking、摘要或 continuation state。（验证：请求捕获测试）
- [x] 提取器要求纯 JSON、最多三条候选；额外文本、错误 schema 和未知目标被拒绝。（验证：提取测试）
- [x] 候选在通知 UI 前已原子落盘，重启后仍可审核。（验证：重启集成测试）
- [x] 候选 ID 可确定性去重，恢复、重绘和重复模型结果不制造重复待审。（验证：去重测试）
- [x] 新建、更新、合并和失效建议均需用户确认。（验证：Coordinator 测试）
- [x] 确认、编辑确认和拒绝均有测试，编辑内容进入 Store 前重新验证。（验证：TUI 与 Store 测试）
- [x] update/merge 展示旧值与新值差异，拒绝不形成活跃记忆。（验证：TUI 测试）

## E. Prompt 与实际记忆效果

- [x] 只有已确认且 active 的记忆进入 `long_term_memory`。（验证：Prompt 测试）
- [x] 索引只含类型、标题、摘要、应用方式和 ID，不含原会话全文。（验证：索引快照测试）
- [x] 合并内容不超过 24 KiB/200 行，项目级冲突优先，两个 scope 均有最低配额。（验证：预算边界测试）
- [x] 超预算只排除 Prompt 输入并显示数量，不删除磁盘记录。（验证：超预算集成测试）
- [x] 确认、编辑、失效和恢复后，Runner 空闲时刷新稳定系统提示词。（验证：Runner 集成测试）
- [x] Runner 忙碌时拒绝替换 Prompt，不污染当前请求。（验证：并发状态测试）
- [x] FakeProvider 证明新会话看到确认记忆，被失效或拒绝的内容不可见。（验证：端到端测试）
- [x] 长期记忆没有写入 Conversation 或一期 `conversation.jsonl`。（验证：会话日志断言）

## F. 生命周期、治理与降级

- [x] `/clear`、`/resume` 和退出通知原 session ID，监听失败不阻断切换。（验证：Session 集成测试）
- [x] 只有至少一个成功轮次的 session 被计数，重复关闭同一 ID 不重复累计。（验证：计数测试）
- [x] 运行中的提取在切换后保留原来源，不误绑定新 session。（验证：并发切换测试）
- [x] 用户级和项目级分别满足“十条 active、二十四小时、五个完成 session”后才治理。（验证：阈值测试）
- [x] 治理只产生 update、merge、inactivate 建议，跨 scope 和 delete 建议被拒绝。（验证：治理 schema 测试）
- [x] 治理失败保留计数并节流，成功后才更新时间和清计数。（验证：失败重试测试）
- [x] Provider、解析、锁、权限、单 scope 或索引失败不阻断正常聊天。（验证：故障注入集成测试）
- [x] 退出取消未完成模型流、等待已开始磁盘提交，且无半写文件或无限等待。（验证：关闭测试）
- [x] 降级 warning 在启动通知或记忆面板持续可见，不被刷新静默吞掉。（验证：TUI 状态测试）

## G. TUI 验收

- [x] 候选只在生成结束、界面空闲且无其他 Modal 时自动弹出。（验证：Textual Pilot 测试）
- [x] `Ctrl+M` 可查看待审、active、inactive、治理建议和 warnings。（验证：面板测试）
- [x] 生成或授权期间打开面板不会抢占现有 Modal。（验证：Modal 冲突测试）
- [x] 键盘导航、Escape、确认、编辑、拒绝、失效、恢复和永久删除均有测试。（验证：Textual Pilot 测试）
- [x] 记忆操作不会清空、复制或错误重绘当前 Conversation。（验证：聊天重绘测试）

## H. 回归、版本与上传证据

- [x] `uv run python -m compileall src tests` 通过。（验证：记录退出码与输出）
- [x] `uv run ruff check .` 通过。（验证：记录退出码与输出）
- [x] `uv run pytest` 完整通过。（验证：记录测试总数与耗时）
- [x] `git diff --check` 通过，版本与锁文件均为 `0.7.0`。（验证：命令输出与文件检查）
- [x] 一期 `0.6.0` 与二期 `0.7.0` 是两个独立、可审查提交。（验证：`git log --oneline`）
- [x] 暂存和提交不含 `hello.txt`、本地配置、session、memory、密钥或其他用户数据。（验证：`git diff --cached --name-only` 与秘密扫描）
- [ ] 只推送 `feature/session-memory` 到 origin，没有直接推送或强推 main。（验证：本地与远端分支检查）
- [ ] 最终报告两个提交 ID、远端分支、完整测试结果和仍未提交的用户文件。（验证：最终验收报告）

## 端到端场景

- [x] 显式开启记忆后，用户表达项目偏好，成功回答触发候选；重启 KCode 后候选仍在，用户确认后开启新会话，Agent Prompt 包含该记忆。（验证：FakeProvider 端到端测试）
- [x] 用户拒绝候选或将已确认记忆设为 inactive，后续新会话不再收到该内容，恢复后重新可见。（验证：状态往返端到端测试）
- [x] Provider 提取失败、项目 Store 锁冲突或单条 Markdown 损坏时，用户仍能完成聊天和恢复会话，并看到明确 warning。（验证：故障注入端到端测试）

## 验收证据

- 2026-08-09：`uv run python -m compileall src tests`，退出码 0。
- 2026-08-09：`uv run ruff check .`，输出 `All checks passed!`。
- 2026-08-09：`uv run pytest`，结果 `280 passed in 22.44s`。
- 2026-08-09：`git diff --check`，退出码 0，无输出。
- 2026-08-09：旧项目命名和旧目录前缀扫描无命中；`/memory` 仅存在于断言其为未知命令的测试。
- 2026-08-09：`git check-ignore -v .kcode/memory/example.md` 命中 `.gitignore` 的 `.kcode/memory/` 规则。
