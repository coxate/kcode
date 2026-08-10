# Slash Command Specification

## 目标

把 KCode 当前散落在 Textual App 中的斜杠命令迁移为独立、可测试的注册与分发系统。斜杠命令只在 idle 状态执行；普通用户消息、`/plan <任务>` 与 `/review [关注点]` 共用同一条 Agent、Conversation 和 journal 提交流程。

## 功能需求

- F1：注册中心统一保存命令名称、别名、描述、usage、类别、参数策略、参数提示、隐藏标记和异步 Handler。
- F2：名称和别名大小写不敏感；启动注册时发现任何交叉冲突立即失败，CLI 返回非零状态并显示冲突值。
- F3：解析器识别 `/` 前缀，以第一个空白分割命令名和参数；参数清理首尾空白但保留内部格式，最长 2000 字符。
- F4：固定别名为 `help → h, ?`、`compact → c`、`plan → p`、`status → s`。
- F5：单独 `/` 等同 `/help`；未知命令提示并引导 `/help`；不允许参数的命令收到参数时显示 usage。
- F6：命令分为 LOCAL、ACTION、PROMPT；所有命令仅在 idle 状态提交。
- F7：迁移 `exit`、`plan`、`do`、`compact`、`resume`、`clear`、`mcp-trust-clear`；`/do` 只切回执行模式，不调用模型。
- F8：`/plan` 只进入 Plan Mode；`/plan <任务>` 先切换模式，再把原始参数作为真实用户消息提交。
- F9：`/compact [重点]` 将参数作为结构化摘要的优先保留主题，不得覆盖固定 JSON 和禁用工具约束。
- F10：只支持 `/mcp-trust-clear`；旧 `/mcp trust clear` 按未知命令处理。
- F11：`/help` 按正式名称排序列出 13 条可见命令；`/help [命令]` 显示完整元数据。
- F12：`/status`、`/memory`、`/permission`、`/session` 提供只读快照；未启用功能要明确说明。
- F13：`/review [关注点]` 发送固定代码审查请求，参数仅作为附加关注点。
- F14：累计每次 `TokenUsageUpdated.request` 的输入与输出 Token；`/clear` 和成功 `/resume` 后归零；字段缺失后对应累计显示 `?`。
- F15：输入满足 `^/\S*$` 时显示正式名称前缀候选，隐藏命令不参与，最多显示 6 行并可滚动。
- F16：完整别名只影响默认高亮，不改变候选集合；例如 `/s` 显示 session/status，默认高亮 status。
- F17：Up/Down 移动高亮；Tab 填入正式命令但不执行；Enter 执行高亮；ESC 关闭并保留输入。
- F18：零匹配显示 disabled 的“无匹配”；参数出现空白时关闭菜单；菜单关闭后不影响输入框或模态页面原有键盘行为。

## 非功能需求

- N1：Registry 是帮助、查找、参数校验和补全的唯一信源；Ready 文案最多提示 `/help`。
- N2：LOCAL 命令不调用 Provider、不写对话历史、不改变会话状态。
- N3：PROMPT 与带参数的 plan 必须复用普通用户消息管线。
- N4：Handler 不导入或暴露 Textual 类型，只依赖 `CommandHost`。
- N5：注册错误在启动期暴露；运行期 Handler 错误转为 notice，不向界面泄漏堆栈。
- N6：压缩 focus 以 JSON 编码，并明确仅是主题；自动和紧急压缩继续传 `None`。
- N7：保持现有 Provider、会话、记忆、权限和状态栏兼容，不重画状态栏。
- N8：Token 累计从精确的 0/0 开始，任一字段未知后该字段保持未知。
- N9：补全菜单在 80×24 与 100×30 下不遮挡输入和状态栏。
- N10：Phase A 与 Phase B 独立实现、独立验收；Phase A 通过后才开始 Phase B。

## 范围外

用户自定义命令、运行期动态注册、Skill 动态命令、命令级权限、flags/tokenizer、鼠标执行、模糊搜索、多行输入、生成期间命令、review 自动读取 diff、记忆编辑、权限编辑、状态栏重设计，以及兼容旧 `/mcp trust clear`。

## 验收标准

1. `/help` 从 Registry 生成按名称排序的 13 条命令。
2. 四组别名及大小写输入均正确分发。
3. 名称、别名及其大小写交叉冲突均阻止启动。
4. 普通文本、空白、`/`、未知命令、原始参数、长度上限和 usage 均按规范处理。
5. LOCAL 命令不请求 Provider、不写历史。
6. 旧 ACTION 行为保持兼容，clear 明确提示且重置累计 Token。
7. plan 的无参数与带参数行为均正确。
8. do 不调用模型。
9. compact focus 被安全透传，固定 JSON、无工具和失败不替换约束保持。
10. MCP 新名称可用，旧名称未知。
11. help 详情完整且支持别名查询。
12. Token 两轮累计、未知值与 clear/resume 重置正确。
13. memory/session 在启用与禁用场景输出正确。
14. review 走统一消息与持久化流程。
15. Handler 异常只显示安全 notice，输入恢复可用。
16. 既有测试无回归。
17. 补全候选、hidden 与别名默认高亮正确。
18. Up/Down/Tab/Enter/ESC 行为正确。
19. 零匹配、参数空白关闭、普通输入及模态键盘无回归。
20. 80×24、100×30 布局与滚动通过人工验收。
