# Kcode MCP 客户端 Checklist

> 状态：工程验收通过；学习复述项待用户逐项证明
> 使用方法：实现完成后逐项检查；每一项必须记录自动化测试、可观察输出或人工验证证据。

## 验收记录（2026-08-06）

- 普通沙箱：181 项测试通过，2 项需要本机监听权限的 HTTP 测试按条件跳过。
- 本机回环集成：stdio、Streamable HTTP JSON、Streamable HTTP SSE 共 3 项通过。
- `ruff check`、`ruff format --check`、`git diff --check` 和凭据模式扫描通过。
- Kcode 版本为 `0.5.0`，锁定 MCP Python SDK `1.29.0`（满足 `>=1.27,<2`）。
- Provider 目录无 MCP 来源分支；`hello.txt` 未被修改。

## 1. 基线与依赖

- [ ] Kcode 版本为 `0.5.0`，启动 Banner 与包版本一致。
- [ ] 锁文件中的 MCP Python SDK 满足 `>=1.27,<2`。
- [ ] 项目没有自研 JSON-RPC 编解码、请求 ID 配对或协议握手实现。
- [ ] 原有 152 项测试全部通过，用户的 `hello.txt` 和其他无关改动未被覆盖。

## 2. 配置加载与故障隔离

- [ ] 用户级与项目级配置都能声明 `mcp_servers`。
- [ ] 不同名 server 被合并，同名 server 由项目级完整覆盖。
- [ ] 项目级只写部分字段时不会从用户级同名 server 补齐其他字段。
- [ ] 缺失配置文件视为空 MCP 配置。
- [ ] 整份 YAML 或核心 Provider 无效时仍明确阻止启动。
- [ ] `mcp_servers` 段无效时只忽略该段并告警。
- [ ] 单个 server 无效时，其他 server 与 Kcode 核心功能继续工作。
- [ ] stdio 缺 command、args/env 类型错误时被跳过。
- [ ] HTTP 缺 URL、协议非法、URL 非绝对地址或含用户名/密码时被跳过。

## 3. 信任边界

- [ ] 用户级 server 不重复询问信任。
- [ ] 每个项目级 stdio/HTTP server 首次启动前都出现信任弹窗。
- [ ] 弹窗展示项目、server、命令或 HTTP endpoint 和变量名称。
- [ ] 弹窗、日志和测试失败信息不展示环境变量值或 headers 值。
- [ ] 拒绝或取消后，没有子进程、DNS/HTTP 请求或环境变量展开。
- [ ] 相同项目和相同配置重启后不重复询问。
- [ ] command、args、URL、env、headers、server 名、类型或项目路径变化后重新询问。
- [ ] 信任文件只保存版本和不可逆指纹，不保存原始配置。
- [ ] 信任文件使用原子写入，并在支持权限位的平台上为 `0600`。
- [ ] 信任文件损坏时按“不信任”处理，并输出安全告警。
- [ ] `/mcp trust clear` 只清除当前项目的记录，并提示重启后重新确认。

## 4. 环境变量与秘密

- [ ] `${TOKEN}`、`Bearer ${TOKEN}` 和一个值中的多个变量都能展开。
- [ ] command、args、URL、server 名和工具名不展开变量。
- [ ] 信任批准之前只扫描变量名称，不读取变量值。
- [ ] 引用缺失变量时只跳过对应 server，并显示缺失变量名。
- [ ] stdio 子进程能看到基础环境和配置 env。
- [ ] stdio 子进程看不到未显式声明的测试 Token。
- [ ] 配置 env 能覆盖同名基础变量。
- [ ] HTTP client 只发送显式 headers，不继承共享 Cookie 或认证。
- [ ] Provider Key 和 MCP 展开值都进入统一脱敏集合。
- [ ] 工具参数、工具结果、远端错误、stderr、启动汇总和模型上下文都不泄漏秘密。

## 5. 连接、发现与命名

- [ ] stdio 完成 transport、initialize、list_tools 和 call_tool。
- [ ] Streamable HTTP 的 JSON 响应完成同样流程。
- [ ] Streamable HTTP 的 SSE 响应完成同样流程。
- [ ] HTTP server 实际收到配置的测试 header。
- [ ] 多个 server 并发启动，总时长不按 server 数量线性叠加。
- [ ] 单 server 启动超过 30 秒时被隔离并报告超时。
- [ ] 一个 server 连接或握手失败不影响其他 server。
- [ ] 工具名为 `mcp__<server>__<tool>`。
- [ ] 含非法字符的完整工具名被跳过并告警。
- [ ] 同一 server 重复工具保留第一个。
- [ ] 不同 server 的同名工具互不覆盖。
- [ ] MCP 工具不能覆盖内置工具。
- [ ] Ready 后工具集合不再变化。

## 6. 动态工具与结果

- [ ] 远端 `inputSchema` 未裁剪地传给 Anthropic 和 OpenAI。
- [ ] 六个内置工具仍使用原有 Pydantic Schema。
- [ ] MCP 调用参数顶层不是对象时在本地拒绝。
- [ ] `readOnlyHint is True` 时 effect 为只读。
- [ ] 缺失、`false` 或非法只读注解时 effect 为有副作用。
- [ ] 多个文本块按返回顺序拼接。
- [ ] 没有文本块的成功结果仍为成功，content 为空。
- [ ] 远端 `isError` 映射为 `mcp_tool_error`。
- [ ] 连接断开或协议异常映射为结构化错误，不逃逸到 Agent Loop。
- [ ] 非文本内容不回灌，并按 server/工具/类型只告警一次。
- [ ] 调用超过 30 秒返回 `timeout`。
- [ ] 用户取消返回 `cancelled`，后续 Agent 轮次仍可继续。

## 7. 权限与 Plan Mode

- [ ] `mcp__github__create_issue` 精确规则可以 allow/deny。
- [ ] `mcp__github__*` 只匹配对应 server 工具。
- [ ] deny 在同一权限层内优先于 allow。
- [ ] MCP 规则不把参数解释成路径或命令。
- [ ] MCP 工具不经过内置命令黑名单和文件沙箱。
- [ ] default/acceptEdits 下只读 MCP 工具无需询问。
- [ ] default/acceptEdits 下副作用 MCP 工具需要询问。
- [ ] bypassPermissions 下 MCP 工具不弹窗。
- [ ] “始终允许”只保存当前完整工具名。
- [ ] Plan Mode 向 Provider 暴露只读 MCP 工具。
- [ ] Plan Mode 不暴露副作用 MCP 工具。
- [ ] 伪造副作用 MCP call 不能绕过 Plan Mode。
- [ ] 原有危险命令、文件沙箱、权限层优先级和四种模式测试保持通过。

## 8. TUI 与用户反馈

- [ ] MCP 初始化期间输入框禁用，状态明确显示正在检查 MCP。
- [ ] 多个项目 server 的信任弹窗顺序出现，不重叠。
- [ ] 单个 server 被拒绝或失败后，后续 server 仍继续处理。
- [ ] Ready 前完成全部允许 server 的连接尝试。
- [ ] 启动汇总显示成功 server 数、工具数、跳过和失败项。
- [ ] 启动汇总不显示内部堆栈或敏感值。
- [ ] 没有 MCP 配置时，启动体验与 v0.4.0 一致。
- [ ] `/help` 包含 `/mcp trust clear`。
- [ ] 未知 `/mcp` 子命令得到明确提示。

## 9. 生命周期与资源回收

- [ ] 每个 server 的 SDK 上下文在同一个 owner task 中进入和退出。
- [ ] `/exit` 后所有 stdio 子进程退出。
- [ ] Ctrl+C 取消后所有 stdio 子进程退出。
- [ ] HTTP session 和 HTTP client 均关闭。
- [ ] 初始化失败时已建立的连接仍被关闭。
- [ ] 正常退出、失败退出和取消退出均没有 pending asyncio task。
- [ ] 单个 server 关闭卡住时，整体关闭不超过 5 秒。
- [ ] manager 开始关闭后拒绝新的 MCP 调用。
- [ ] 连接失效后不自动重连，调用返回结构化错误。

## 10. 跨 Provider 与回归

- [ ] Anthropic/OpenAI 收到相同名称、描述和参数 Schema。
- [ ] 两个 Provider 对 MCP ToolResult 的回灌语义一致。
- [ ] Provider 源码不存在按 MCP 来源分支处理的逻辑。
- [ ] 多轮 Agent Loop、流式输出、缓存、计划、审批、保序回灌和取消测试通过。
- [ ] 只读 MCP 工具遵守现有最大并发数。
- [ ] 副作用 MCP 工具保持保守串行执行。

## 11. 自动化质量门槛

```bash
UV_CACHE_DIR=/tmp/kcode-uv-cache uv run pytest tests/test_mcp_*.py -q
UV_CACHE_DIR=/tmp/kcode-uv-cache uv run pytest -q
UV_CACHE_DIR=/tmp/kcode-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/kcode-uv-cache uv run ruff check .
git diff --check
git grep -nE '(token|api[_-]?key)[=:][[:space:]]*[A-Za-z0-9_-]{12,}' -- ':!uv.lock'
```

- [ ] MCP 专项测试全部通过。
- [ ] 完整测试套件全部通过，无挂起或死锁。
- [ ] Ruff 格式与检查通过。
- [ ] `git diff --check` 通过。
- [ ] 配置、信任记录、日志和测试夹具没有凭据明文。
- [ ] 手动 stdio 与 HTTP 冒烟测试通过。

## 12. 验收覆盖

- [ ] AC1–AC5：配置、信任、插值与最小环境均有自动化测试证据。
- [ ] AC6–AC10：两种 transport、SDK、动态工具、命名和结果均有证据。
- [ ] AC11–AC15：权限、并发、取消、关闭和启动反馈均有证据。
- [ ] AC16–AC18：跨 Provider、安全扫描和完整回归均有证据。

## 学习与面试记录

- [ ] 用户能够解释为什么“工具调用授权”不能替代“server 启动信任”。
- [x] 用户能够解释配置指纹为何基于未展开配置且不保存秘密。
- [ ] 用户能够解释 owner task 如何避免跨 task 关闭异步上下文。
- [ ] 用户能够解释为什么缺失 `readOnlyHint` 必须按有副作用处理。
- [ ] 明确区分用户亲自实现/理解的部分与 Codex 协助完成的部分。
