# Kcode SubAgent Checklist

> 状态：已批准。每项必须通过运行代码或观察真实行为验证。

## 功能验收

- [ ] AC1 定义解析：合法最小定义可加载；未知字段、非法名称、权限模式、轮次和空正文只跳过自身，warning 不泄漏正文。
- [ ] AC2 路径安全：符号链接、目录逃逸、二进制、非法 UTF-8 和超过 32 KiB 的文件均被拒绝。
- [ ] AC3 Catalog：项目覆盖用户、用户覆盖内置、内置覆盖插件，最终最多 30 项。
- [ ] AC4 插件来源：只扫描用户插件目录下合法的 plugin-id/agents/*.md，非法路径不能逃逸。
- [ ] AC5 项目信任：首次及内容变化重新确认；拒绝不影响内置和用户角色。
- [ ] AC6 权限字段：项目 bypassPermissions 被拒绝；用户及插件版本加载时明确警告。
- [ ] AC7 内置资源：wheel 包含 general-purpose、explore、plan，后两者只含真实只读工具。
- [ ] AC8 配置安全：用户级配置生效；项目级被忽略并 warning；旧配置继续加载。
- [ ] AC9 Provider：inherit 复用父 Provider；命名 Provider 懒加载并复用；调用参数不能临时切换。
- [ ] AC10 稳定工具：主 Agent 始终看到 agent 和四个 task 工具，Catalog 变化不改变 schema。
- [ ] AC11 定义式隔离：Conversation、ContextManager、Session、HookSession、SkillRuntime 和 Token 独立。
- [ ] AC12 权限上限：子角色只能收紧父模式；Plan Mode 不能通过 SubAgent 间接写文件。
- [ ] AC13 工具过滤：白名单、黑名单、后台基础集合和显式 MCP 正确；load_skill 不扩权。
- [ ] AC14 Fork：首轮保留父稳定 Prompt、历史前缀、Provider 和完整工具 schema。
- [ ] AC15 嵌套阻断：Fork 中控制工具 schema 稳定但执行被拒绝；定义式和 Skill Fork 中不可见。
- [ ] AC16 前台运行：定义式结果作为普通 agent 工具结果返回主模型。
- [ ] AC17 后台路径：显式、配置超时和 Esc 都不取消子 Runner；Fork 始终后台。
- [ ] AC18 取消：Ctrl+C 取消未脱离父子任务；已脱离任务只由 task_stop 或退出取消。
- [ ] AC19 任务限制：最多 4 个运行或等待审批，最多保留 20 个，安全淘汰正确。
- [ ] AC20 任务控制：list/get/stop/send_message 使用唯一 task ID，错误状态结构化。
- [ ] AC21 后台审批：FIFO 排队、来源清晰、前台生成结束后显示、取消任务撤销请求。
- [ ] AC22 通知：完成、失败和取消产生一次性提醒；脱敏、限制 32 KiB、不写 JSONL。
- [ ] AC23 用量：子 Token 计入任务及 UI session 费用，不改变主 ContextManager 锚点。
- [ ] AC24 Hook Agent：仅异步定义式有效；同步、拦截、未知角色和子 Agent 递归被拒绝。
- [ ] AC25 Skill 回归：/review 继续前台显示、支持取消、完整回流历史和长期记忆。
- [ ] AC26 关闭清理：退出时先取消任务和审批，再关闭 Hook、MCP 与 session，无悬挂协程。
- [ ] AC27 启动顺序：有无 MCP 及 MCP 失败时，输入只在 Agent 最终校验和 Hook 绑定后启用。
- [ ] AC28 安全输出：warning、ToolResult、通知、日志和信任文件不包含角色正文、API Key 或敏感值。

## 自动化检查

- [ ] 新增的 parser、catalog、trust、config、provider、filter、factory、approval、manager、tools 和 UI 定向测试通过。
- [ ] 受影响的 agent loop、Skill、Hook、App、MCP 和 history 定向测试通过。
- [ ] 全仓使用临时 UV cache 运行 uv run pytest 通过，数量高于 367 passed, 2 skipped。
- [ ] uv run ruff check . 通过。
- [ ] 本次新增和修改 Python 文件的 ruff format --check 通过。
- [ ] git diff --check 无输出。
- [ ] wheel 构建成功并包含三个内置 Agent Markdown。
- [ ] Git diff 只包含 SubAgent 文档、实现、测试及必要集成接缝。

## tmux 端到端

- [ ] 使用临时项目和临时 KCODE_SUBAGENT_TRUST_PATH，不修改真实信任文件或 HOME。
- [ ] 首次项目 Agent 显示信任界面；修改内容重启后再次确认。
- [ ] Available Agents 只显示名称和描述，不泄漏正文。
- [ ] explore 从空历史开始且不能使用写工具；定义式前台任务返回完整 ToolResult。
- [ ] 显式后台立即返回 task ID，临时短超时触发自动转后台。
- [ ] 前台任务按 Esc 转后台，Ctrl+C 仍为取消。
- [ ] Fork 保留父前缀并报告真实 cache usage 字段。
- [ ] 后台写操作进入带 task 来源的审批队列。
- [ ] task list/get/stop/send_message 行为正确。
- [ ] Hook agent action 创建定义式后台任务，子 Hook 不产生递归。
- [ ] /review Skill Fork 行为与当前版本一致。
- [ ] 正常退出后无后台任务、审批或 Worker 残留。
- [ ] session JSONL 不含 task-notification、角色正文或敏感信息。

> 涉及真实模型调用的端到端测试可能产生 API 费用；执行前必须明确说明范围。
