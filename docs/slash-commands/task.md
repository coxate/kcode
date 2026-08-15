# Slash Command Tasks

- [x] T1 定义 CommandType、ArgumentPolicy、CommandSpec、ParsedCommand、CommandContext 与快照模型。
- [x] T2 实现 CommandHost 协议、CommandRegistrationError、CommandRegistry。
- [x] T3 实现 CommandDispatcher 的识别、参数验证和安全错误转换。
- [x] T4 注册 13 条内置命令和四组固定别名。
- [x] T5 实现 help、status、memory、permission、session Handler。
- [x] T6 实现 exit、plan、do、compact、resume、clear、mcp-trust-clear Handler。
- [x] T7 实现 review 固定提示词与可选关注点。
- [x] T8 在 KCodeApp 提取并接入统一 `_submit_user_text()`。
- [x] T9 在 KCodeApp 实现 CommandHost 适配方法并删除旧命令分支。
- [x] T10 实现会话 Token 累计与 clear/resume 重置。
- [x] T11 透传并安全编码 compact focus。
- [x] T12 在 CLI 启动 TUI 前构造并验证 Registry。
- [x] T13 完成 Phase A 单元和集成测试。
- [x] T14 运行 Phase A pytest、ruff、compileall 并修复回归。
- [x] T15 实现非聚焦 OptionList 补全菜单及候选更新。
- [x] T16 实现 Up/Down/Tab/Enter/ESC 与别名默认高亮。
- [x] T17 完成 Phase B 自动化测试与两种终端尺寸验收。
- [ ] T18 运行全量检查并完成 tmux 命令链人工验收。
