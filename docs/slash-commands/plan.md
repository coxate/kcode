# Slash Command Implementation Plan

## Phase A：命令核心

1. 新建 `kcode.commands`，定义命令模型、Host 协议、注册错误、Registry、Dispatcher 和内置命令工厂。
2. Registry 在启动期完成全部注册并冻结；名称、别名、帮助、参数验证和未来补全都读取同一份元数据。
3. 在 `KCodeApp` 中实现 `CommandHost` 适配层，并提取唯一 `_submit_user_text()`，供普通文本、review 和带任务的 plan 共用。
4. 删除旧 `ui/commands.py` 和 `_run_command()` 分支，保留原会话、记忆、权限、MCP 和渲染组件的职责。
5. 新增会话级 Token 累计器，并在 clear、成功 resume 后归零。
6. 将 compact focus 经 `AgentRunner → ContextManager → CompactionEngine → build_compaction_prompt` 透传；自动和紧急压缩保持 `None`。
7. CLI 在 TUI 启动前构造 Registry，注册冲突时显示错误并返回非零状态。
8. 增加单元、集成和回归测试，运行 pytest、ruff 与 compileall。

## Phase B：自动补全

1. 在输入框和状态栏之间加入不获取焦点的 OptionList 菜单。
2. 监听单行 Input 变化，用 Registry 的正式名称前缀生成最多 6 行候选。
3. 通过条件化 App action 处理 Up/Down/Tab/ESC；Enter 继续由 Input.Submitted 接管并执行当前高亮。
4. 确保菜单关闭时不拦截普通 Input、ResumeScreen 和 MemoryScreen 的原行为。
5. 增加键盘、候选、零匹配、模态回归和尺寸测试，并进行 tmux 人工验收。

## 风险与回退

- 消息管线重构可能造成 journal 或 Provider 调用次数变化；通过复用一个提交函数和现有集成测试约束。
- Textual 键盘绑定可能影响模态页面；绑定只在主屏幕、prompt 聚焦且菜单可见时生效。
- 压缩 focus 可能形成提示词注入；使用 JSON 编码，并在固定提示词中声明其只可作为主题。
- 两阶段分别提交；若 Phase B 有回归，可只回退补全 UI，不影响 Phase A 命令核心。
