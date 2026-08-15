# Kcode Hook 生命周期挂钩系统 Checklist

> 审批状态：已批准。实现后逐项记录实际证据。

## 功能验收

- [x] 两层配置、重复ID、严格解析、预算与安全降级正确。
- [x] 项目首次/变化确认，拒绝、存储失败及 Skill/MCP 信任隔离正确。
- [x] agent/subagent 配置被拒绝，源码无占位执行器。
- [x] 15个事件字段和 startup/session/clear/resume/shutdown 顺序正确且不重复。
- [x] 四操作符、AND/OR、转义、字段访问与非法表达式正确。
- [x] 六类变量、`$$`、shell-safe 单次展开与敏感值脱敏正确。
- [x] command cwd、成功/失败、输出预算、timeout、取消和进程组清理正确。
- [x] prompt 进入正确请求、消费一次且不入 Conversation/JSONL/记忆。
- [x] HTTP 本地服务收到正确请求；错误、超时和响应超限只 warning。
- [x] reject 不审批、不执行、回灌原因；附加 action 失败仍拒绝，首拒绝停止。
- [x] 所有 ToolResult 触发 post-tool，仅成功 write/edit 触发 file-change。
- [x] 未拒绝工具保留权限；bypass不能绕过；permission Hook不能批准。
- [x] once 在 session 内一次，clear/resume/重启重置且不持久化。
- [x] async 仅非拦截 command/HTTP，最多8个，无未处理异常。
- [x] Plan Mode prompt/reject生效，command/HTTP零副作用。
- [x] Skill fork 共享拦截/once/并发，不重复进程和会话事件。
- [x] compact/error/command生命周期正确且Hook错误不递归。
- [x] `/hooks` 帮助、补全、安全摘要、空状态正确；默认帮助17条。
- [x] 有/无MCP、信任批准/拒绝都在收尾后启用输入。
- [x] 无Hook时Slash、Skill、Plan、权限、MCP、历史、记忆回归不变。

## 自动化

- [x] Hook定向测试全部通过（164 passed）。
- [x] 全仓 `UV_CACHE_DIR=/private/tmp/kcode-hook-uv-cache uv run pytest` 高于 `321 passed, 2 skipped`（367 passed, 2 skipped）。
- [x] `uv run ruff check .` 通过。
- [x] 本次 Python 文件 `ruff format --check` 通过（37 files）。
- [x] `git diff --check` 无输出且 diff 无无关改动。
- [x] `uv build --out-dir /private/tmp/kcode-hook-dist` 成功，wheel 已安装冒烟并导入 `kcode.hooks`。

## tmux 端到端

- [x] 使用 `mktemp -d`、临时 `KCODE_HOOK_TRUST_PATH`、本地假Provider和localhost HTTP，不改HOME、不产生模型费用。
- [x] 首次项目确认、拒绝、批准、内容变化再确认正确。
- [x] session prompt与once正确，clear/resume后重置，JSONL无正文和状态。
- [x] file_change自动格式化；失败只warning。
- [x] write/run_command危险调用被拒绝，模型看到原因后调整。
- [x] HTTP async不阻塞，失败安全降级。
- [x] Plan Mode无command/HTTP副作用，Do Mode恢复。
- [x] `/hooks`、`/help`、inline/fork Skill正确。
- [x] Ctrl+C清理进程和任务，正常退出顺序正确，无异常栈/孤儿进程/敏感泄漏。

## 实跑证据

- 临时目录：`/private/tmp/kcode-hook-e2e.fFhfVS`；活动 Provider 为 `127.0.0.1` 的 OpenAI 兼容假服务。
- 项目信任先拒绝得到 `No hooks loaded.`，再批准加载5条 Hook；修改配置注释并重启后再次出现确认界面。
- 危险 `touch blocked.txt` 返回 `hook_rejected`，文件不存在；成功 `write_file` 后生成 `.hook-formatted`。
- Plan Mode 前后 HTTP 日志行数保持7，Do Mode 恢复通知；`/help` 实际显示17条命令。
- `/clear` 和成功 `/resume` 后首个请求均重新包含 session prompt；session JSONL 未出现 Hook 正文、once 或 pending 状态。
- 60秒 localhost 延迟请求按 Ctrl+C 后显示“用户已取消当前任务”，输入恢复；结束后 `tmux list-sessions` 确认无服务和会话残留。
