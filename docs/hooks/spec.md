# Kcode Hook 生命周期挂钩系统 Spec

> 审批状态：已批准。

## 背景与目标

Kcode 已具备 Slash Command、Skill、权限审批、工具调度、会话持久化和上下文压缩，但格式化、危险操作拦截、会话提示和外部通知仍需人工重复执行。Hook 提供声明式的“事件 + 条件 + 动作”，横跨进程、会话、Agent、Provider、权限和工具生命周期，且不属于、也不依赖 Skill。

目标：

- 提供 `session_start/session_end`、`turn_start/turn_end`、`pre_tool_use/post_tool_use`、`pre_send/post_receive`、`startup/shutdown/error/compact/permission_request/file_change/command_execute` 15 个事件。
- 从 YAML 严格加载 Hook，支持 `==`、`!=`、`=~`、`~=` 与不可混用的 `&&`、`||`。
- 实现 `command`、`prompt`、`http` 三种 action；不声明 `agent/subagent`。
- 支持 `$EVENT`、`$TOOL_NAME`、`$FILE_PATH`、`$MESSAGE`、`$ERROR`、`$TOOL_ARGS.xxx`。
- 支持 session 内 `once`、受限 `async` 和 action timeout。
- `pre_tool_use + reject:true + reason` 在权限判断前返回工具错误，且不能被 bypassPermissions 绕过。
- Hook 普通失败 fail-open；明确 reject fail-closed；取消必须传播。

## 功能需求

### 配置与信任

- **F1**：加载 `~/.kcode/hooks.yaml` 与 `<project>/.kcode/hooks.yaml`，用户规则先加入、项目规则后追加，不做覆盖。
- **F2**：项目文件按规范化项目路径、相对路径和原始字节计算指纹，首次发现或内容变化时确认。
- **F3**：信任独立保存到 `~/.kcode/hook-trust.json`；拒绝或存储异常时跳过项目 Hook 并继续启动。
- **F4**：顶层必须为 `hooks` 数组；字段限定为 `id/event/if/action/reject/reason/once/async`，未知或非法字段只跳过当前规则。
- **F5**：ID 全局唯一，冲突时保留先加载项。普通 Hook 必须有 action；纯 reject 可省略 action。
- **F6**：配置仅启动期加载，不支持热更新。

### 生命周期

- **F7**：`startup` 在配置、信任、MCP、命令完成后且输入启用前触发；初始顺序为 `startup → session_start`。
- **F8**：正常退出顺序为 `session_end → shutdown`；`/clear` 与 `/resume` 均结束旧 session，再开始新/恢复 session。
- **F9**：`turn_start` 在模型型任务开始时触发；`turn_end` 在完成、失败、限制或取消时恰好触发一次。
- **F10**：`pre_send` 在每次 Provider 请求前，`post_receive` 在完整响应后。
- **F11**：`pre_tool_use` 在参数校验后、权限判断前；`post_tool_use` 在每个最终 ToolResult 后。
- **F12**：Ask 审批显示前触发 `permission_request`；只有成功的 `write_file/edit_file` 触发 `file_change`。
- **F13**：手动、自动或紧急压缩完成后触发 `compact`；Provider、工具、命令或压缩错误触发 `error`，Hook 自身错误不递归触发。
- **F14**：合法 Slash Command 在 handler 前触发 `command_execute`，Hook 不能阻止命令。
- **F15**：Skill fork 继承相同 Hook 拦截和 session 状态，但不重复进程/会话事件。

### 条件与上下文

- **F16**：`if` 可省略；支持精确、不等、正则搜索、完整 glob 匹配。
- **F17**：同一表达式只允许全 AND 或全 OR，不支持混用、括号和优先级。
- **F18**：字段限定为 `event/tool/file_path/message/error/command/args.xxx`；缺失运行期值为空。
- **F19**：字符串使用引号，正则使用 `/pattern/`；非法转义、字段和正则加载期拒绝。
- **F20**：HookContext 含 session、cwd、mode 和事件特化字段；对象/数组用稳定 JSON 表示。
- **F21**：模板支持六类变量；`$$` 表示字面量 `$`；替换结果不二次展开。
- **F22**：command 使用 shell-safe 替换；prompt、HTTP、reason 使用普通文本替换；已知敏感值先脱敏。

### Action 与执行控制

- **F23**：command 在项目 cwd 启动 shell，捕获有限输出，默认30秒、可配0.1–300秒，超时/取消终止进程组。
- **F24**：prompt 加入下一次 Provider 请求的临时 reminder；`pre_send` prompt 进入当前请求；不写 Conversation、JSONL 或记忆。
- **F25**：HTTP 支持 http/https、method、headers、body 和 timeout；无 body 时发送稳定 HookContext JSON；失败只 warning。
- **F26**：action 只允许 command/prompt/http；agent/subagent 为未知类型。
- **F27**：reject 只允许 pre_tool_use，必须提供 reason；拒绝跳过权限和真实工具，形成 `hook_rejected` 错误 ToolResult。
- **F28**：拒绝后仍触发 post_tool_use，但不触发 file_change；首个 reject 停止后续 pre-tool Hook。
- **F29**：once 在当前 session 首次实际尝试、成功调度或拒绝后标记；clear、resume、重启重置且不持久化。
- **F30**：async 只允许非拦截 command/HTTP，最多8个在途任务；prompt、reject 和所有 pre-tool Hook 禁止 async。
- **F31**：Plan Mode 仍执行 prompt/reject，跳过 command/HTTP；Hook 不能 Allow、自动审批或降低权限。

### 可观察性

- **F32**：新增 `/hooks`，显示 ID、事件、action 摘要、flags 和来源，不显示正文与敏感配置；空状态为 `No hooks loaded.`。
- **F33**：warning 只含安全摘要；加载、同步、异步和退出出口统一脱敏和截断。
- **F34**：无 Hook 时现有 Agent、Slash、Skill、MCP、权限、历史和记忆行为不变。

## 非功能需求

- YAML 安全解析；只接受边界内普通 UTF-8 文本文件，拒绝符号链接和二进制。
- 单文件最大256 KiB、最多100条 Hook、单模板32 KiB、单请求 prompt 64 KiB、action 输出32 KiB、拒绝原因2 KiB。
- 相同配置得到稳定顺序；超限不污染旧状态。
- Hook 配置不主动获得 Provider secret；所有外显信息使用现有敏感值脱敏。
- 用户取消和 App 退出必须清理子进程、HTTP 与后台任务。
- Hook 状态不修改 session schema；真实 Hook prompt Token 计入 Provider 用量但不计入持久历史增长。
- 无 MCP、MCP 失败、信任拒绝和配置损坏均安全降级，输入只在启动收尾后启用。
- 全仓测试、lint、构建和真实 tmux 端到端通过；当前基线为 `321 passed, 2 skipped`。

## 不做的事

- 不实现或占位 agent/subagent action。
- 不持久化 once/prompt，不做热加载、优先级、依赖、互斥、重试或独立日志数据库。
- 不支持复杂表达式、修改模型响应/工具参数、自动批准或其他事件拦截。
- 不升级权限 YAML 公开语法，不做 OS 文件监听，不推断 shell 文件副作用。
- 不做远程 Hook 安装、include、继承或 Skill 内嵌 Hook。

## 验收标准

1. 两层配置、严格解析、预算、ID 冲突和独立项目指纹信任可观察且安全降级。
2. 15 个事件在固定节点、固定顺序触发，clear/resume/shutdown 不重复。
3. 四种操作符、AND/OR、嵌套字段、变量、`$$` 和 shell-safe 展开正确。
4. command、prompt、HTTP 的成功、失败、超时、取消、输出预算和脱敏正确。
5. pre-tool reject 不审批、不执行、向模型回灌原因；附加 action 失败仍拒绝。
6. post-tool、file-change、permission、compact、error 和 command 生命周期正确。
7. once、async、Plan Mode、bypassPermissions 与 Skill fork 边界正确。
8. `/hooks` 进入帮助和补全；默认三个内置 Skill 时 `/help` 为17条。
9. 无 Hook 配置时全部既有行为回归不变。
10. tmux 以临时信任路径、本地 HTTP 和假 Provider 完整实跑，无费用、异常栈、孤儿进程或敏感泄漏。
