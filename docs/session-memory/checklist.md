# KCode 项目指令与会话持久化 Checklist

> 每一项必须通过运行代码或观察行为验证；实现前保持未勾选，验收时记录实际证据。

## 实现完整性

- [x] 三层 `KCODE.md` 缺失、单独存在、同时存在时均正确加载，冲突按本机项目 > 项目 > 用户处理。（验证：`uv run pytest tests/test_instructions.py -q`）
- [x] include 的嵌套、重复、环路、深度、路径逃逸、符号链接、坏编码和 32 KiB 上限均产生预期内容或可见警告。（验证：`uv run pytest tests/test_instructions.py -q`）
- [x] session ID、JSONL header 和三种内部消息格式符合 schema v1，Provider continuation、system 和 reminder 不落盘。（验证：`uv run pytest tests/test_history_codec.py -q`）
- [x] 空白会话不落盘；首个检查点创建目录、ignore、header 和消息；后续批次有序追加并 fsync。（验证：`uv run pytest tests/test_history_journal.py -q`）
- [x] 日志和目录权限在支持的平台收紧；已知敏感值不出现在 JSONL；文档明确剩余明文风险。（验证：Journal 测试 + README 检查）
- [x] 两个进程不能同时续写同一会话，持锁进程退出后可重新获取。（验证：Journal 多进程测试）

## 恢复与状态切换

- [x] `/resume` 的列表、搜索和预算内恢复完全本地处理，不列出当前 runtime；超预算时只按 F18 调用当前 Provider 压缩。其他列表字段、倒序、搜索、键盘选择和 Esc 均正确。（验证：`uv run pytest tests/test_resume_ui.py -q`）
- [x] 纯文本和完整工具链恢复后，下一次 FakeProvider 请求收到同顺序规范消息。（验证：`uv run pytest tests/test_session_persistence_integration.py -q`）
- [x] 坏行和半行结尾不会丢掉其他有效记录，并向用户报告跳过数量。（验证：`uv run pytest tests/test_history_store.py -q`）
- [x] 未知 schema、header/目录身份不一致和越界 session ID 被拒绝，不影响其他会话。（验证：`uv run pytest tests/test_history_store.py -q`）
- [x] 缺失工具结果只在内存补“状态未知”，孤立结果不发送，恢复前后 JSONL 字节一致。（验证：`uv run pytest tests/test_history_store.py -q`）
- [x] 当前模型与原模型不同时仍可恢复并显示差异，不自动切换 Provider。（验证：runtime 与 App 测试）
- [x] 超预算历史复用现有 ContextManager 压缩；失败时旧 runtime 保持可用，日志不变。（验证：`uv run pytest tests/test_history_runtime.py -q`）
- [x] 恢复后首个 Agent 轮次包含临时过期提醒，多次工具迭代均携带，第二轮和 JSONL 中没有。（验证：集成测试）
- [x] 恢复界面重绘用户和最终助手轮次，只显示工具摘要，不展开完整历史输出。（验证：Textual pilot）
- [x] 恢复成功后继续追加原会话；目标 busy、损坏或压缩失败时不关闭当前会话。（验证：runtime + UI 测试）
- [x] `/clear` 保留旧日志并切换新 ID，旧会话仍能再次 `/resume`。（验证：App 集成测试）
- [x] 写盘失败后对话仍可用且警告持续可见，不显示虚假的完整保存。（验证：Journal 故障注入 + App 测试）
- [x] 正常退出写 `session_end` 并释放锁；异常退出的旧检查点仍可恢复且显示提示。（验证：子进程测试）

## 兼容与质量

- [x] 没有指令/历史、旧 session ID、仅有旧 `tool-results` 的项目正常启动，旧数据不迁移、不展示、不删除。（验证：兼容测试）
- [x] 原 Provider、MCP、权限、工具、Prompt、Context 和 TUI 测试全部通过。（验证：`uv run pytest -q`）
- [x] 源码可编译且 lint 通过。（验证：`uv run python -m compileall src`；`uv run ruff check .`）
- [x] 测试没有真实 API、网络或费用，日志 fixture 不含真实秘密。（验证：FakeProvider/monkeypatch + 测试配置检查）
- [x] 文档中的路径、接口、命令和版本与实现一致，无其他项目命名和未完成标记。（验证：文档一致性扫描）

## 端到端场景

- [x] 启动加载三层规则 → 完成含工具调用的会话 → 正常退出 → 再启动搜索恢复 → 显示模型/过期提示 → 继续对话 → `/clear` 新建会话，全流程可观察且数据文件符合预期。（验证：FakeProvider 端到端测试并记录实际文件与界面结果）

## 验收证据（2026-08-08）

- `uv run python -m compileall -q src`：退出码 0。
- `uv run ruff check .`：`All checks passed!`。
- `uv run pytest -q`：`246 passed in 19.24s`。
- `uv run pytest tests/test_resume_ui.py::test_phase_one_end_to_end_through_tui -q`：`1 passed`；覆盖三层规则、工具会话、正常关闭、TUI 搜索恢复、跨模型与过期提醒、继续会话和 `/clear`。
- 所有 Provider 流均为 FakeProvider/脚本事件；测试未读取真实 API Key、未访问网络、未产生模型费用。
