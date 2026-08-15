# Slash Command Acceptance Checklist

## Phase A

- [x] 13 条命令全部由 Registry 提供，帮助按正式名称排序。
- [x] 冲突注册导致 CLI 非零退出并显示冲突值。
- [x] `/`、未知命令、大小写、别名、参数格式和 2000 字符上限正确。
- [x] LOCAL 命令不调用 Provider、不写历史。
- [x] `/plan`、`/plan <任务>`、`/do` 和 `/review` 行为正确。
- [x] clear、resume、compact、exit、MCP 命令无回归。
- [x] `/mcp trust clear` 已不兼容。
- [x] status、memory、permission、session 对启用/禁用状态说明明确。
- [x] 会话 Token 正确累计，并在 clear/成功 resume 后归零。
- [x] compact focus 不影响固定 JSON 和禁用工具约束。
- [x] Handler 异常不泄漏堆栈，输入仍可用。
- [x] `uv run pytest` 通过（291 passed, 2 skipped）。
- [x] `uv run ruff check .` 通过。
- [x] `uv run python -m compileall src` 通过。

## Phase B

- [x] 菜单只在 `^/\S*$` 输入下显示，正式名称前缀、hidden 和六行限制正确。
- [x] 完整别名只改变默认高亮，不改变候选集合。
- [x] Up/Down/Tab/Enter/ESC 与零匹配行为正确。
- [x] 参数空白关闭菜单，普通输入和模态页面键盘无回归。
- [x] 80×24、100×30 布局和内部滚动正常。
- [x] 全量 pytest、ruff、compileall 通过。

## 最终人工链路

- [ ] `/help → /status → /plan 规划任务 → /do → /review 并发安全 → /clear → /resume`
- [ ] 界面不卡顿，状态和 Token 正确，消息正常持久化，旧会话可恢复，无异常日志。
