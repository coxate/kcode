# Kcode MCP 客户端 Plan

> 状态：已批准

## 架构概览

在现有配置、工具注册表和 Textual 应用之间增加 MCP 子系统。配置层只负责解析、合并和保留未展开的安全配置；信任层在任何秘密展开、进程启动或网络连接之前决策；连接管理器为每个 server 启动一个长期所有者任务；工具包装层把活动会话转换为 Kcode `Tool`；权限层把现有六工具特例泛化为“内置工具目标”和“外部工具目标”两类。

启动数据流：

```text
读取两层配置
  → 校验并按 server 合并
  → 项目级配置逐个信任确认
  → 展开 env/headers 并收集敏感值
  → 并发启动 server owner task
  → initialize + list_tools
  → 注册 MCP 工具并冻结快照
  → 启用聊天输入并显示启动汇总
```

退出数据流：

```text
Textual Unmount
  → 设置全部 server stop event
  → owner task 在创建它的 task 内退出 SDK 上下文
  → 最多等待 5 秒
  → 取消并回收仍未退出的 owner task
```

## 核心数据结构与接口

### MCP 配置

- `StdioMcpServerConfig`：`name`、`source`、`command`、`args`、原始 `env`。
- `HttpMcpServerConfig`：`name`、`source`、验证后的 URL、原始 `headers`。
- `McpConfigSet`：合并后的 server 列表、配置告警。
- `ResolvedMcpServerConfig`：完成信任和变量展开后的启动配置、敏感值集合。
- `source` 取 `user` 或 `project`；只有 `project` 进入信任流程。

Pydantic 使用可辨别联合校验 stdio/http。`AppConfig` 增加 `mcp_servers` 和 `mcp_warnings`，核心 provider 校验仍保持失败即停止。合并函数对 provider 保持现状，对 MCP 按 server 完整覆盖。

### 信任

- `McpTrustRequest`：只包含允许展示的项目、server、命令或 URL 摘要，以及环境变量名称。
- `McpTrustStore`：默认保存到 `~/.kcode/mcp-trust.json`，格式含版本号和 SHA-256 指纹集合。
- 指纹输入为规范化项目绝对路径、server 名和未展开配置的稳定 JSON；不包含展开后的值。
- 保存使用同目录临时文件、原子替换和仅当前用户可读写权限。
- `/mcp trust clear` 清除当前项目的所有 MCP 信任；新配置在下一次启动重新确认。

### 动态工具

- `ToolSpec` 增加可选的显式 `parameters` JSON Schema；内置工具未提供时仍由 Pydantic 模型生成。
- 新增允许任意 JSON 对象字段的 `McpToolArguments`，只负责保证顶层是对象；远端 schema 原样提供给模型，server 仍是调用参数语义的最终校验者。
- `McpTool` 保存完整名、远端原名、server handle、schema 和 `ToolEffect`。
- 成功结果统一为 `ToolResult.success({"content": text})`；远端错误、传输错误和非文本告警映射到现有结果结构。

### 连接管理

- `McpManager.prepare()`：顺序请求项目级信任，之后才展开变量；返回允许启动的配置和安全告警。
- `McpManager.connect_all()`：为允许的 server 并发创建 owner task，并等待每个任务报告 ready/failed，单任务 30 秒超时。
- owner task 在同一 task 内进入和退出 SDK 传输与 `ClientSession` 上下文，避免跨 task 关闭 AnyIO cancel scope。
- `McpServerHandle.call_tool()`：序列化访问活动 session、实施 30 秒调用超时，并把异常转为结构化结果。
- `McpManager.close()`：设置停止事件并回收 owner task，整体 5 秒超时。
- SDK 依赖固定为稳定 v1 范围 `mcp>=1.27,<2`，升级到 v2 作为独立迁移任务。

## 模块设计

### 配置与秘密边界

现有配置读取器保留原始 MCP 字符串，使用 `${VAR}` 全局插值器提取变量名。信任通过后才读取宿主环境。缺失变量只淘汰对应 server。stdio 基础环境采用跨平台允许列表，包括 `PATH`、用户目录、临时目录、系统根和 locale 变量；配置 env 最后覆盖。展开出的完整值加入应用级敏感值，并与 Provider API Key 一起交给工具结果脱敏。

### MCP 子系统

新增 `kcode.mcp` 包，分为配置解析后的运行模型、信任存储、连接管理和工具包装。stdio 使用官方 `stdio_client`；HTTP 构造只含配置 headers 的独立异步 HTTP 客户端，再交给 `streamable_http_client`。不注册 sampling、roots 或通知回调。

工具发现严格验证最终完整名。重复工具保留第一个。manager 返回工具、汇总和告警，由注册表在进入 Ready 前一次性注册；运行期不再修改。

### 权限泛化

保留危险命令、Plan Mode 命令判断和路径沙箱的现有代码路径。对 MCP 工具构造通用权限目标：友好名等于完整工具名、匹配值为空、副作用来自 `ToolSpec.effect`。

权限规则模型的工具名从固定 Literal 泛化为字符串；解析器继续支持六个内置名字及其参数规则，同时允许 `mcp__...` 名称和名称中的 `*`。MCP 规则只匹配工具名，不匹配参数。执行器不再对所有未知类别无条件询问，而是按工具 effect、规则和权限模式决策。Plan Mode 的定义集合为现有四工具加所有只读 MCP 工具。

### Textual 生命周期与交互

`KCodeApp` 在挂载后启动独占的 MCP 初始化 worker，期间禁用输入并显示“正在检查 MCP”。项目 server 的信任使用新的 `McpTrustScreen` 顺序展示，避免多个并发弹窗。确认结束后并发连接 server；全部尝试结束后更新应用敏感值、注册工具、显示汇总并启用输入。

应用卸载时屏蔽取消并等待 manager 关闭，最多 5 秒。若 MCP 未配置，启动路径与当前行为一致。CLI 继续使用现有同步入口，MCP SDK 生命周期完全位于 Textual 的事件循环内。

### 配置示例和版本

示例配置增加一个 stdio 和一个 HTTP server，秘密全部使用 `${VAR}`。包版本从 `0.4.0` 升到 `0.5.0`，锁文件记录 MCP SDK 及其传递依赖。

## 文件组织

```text
src/kcode/
├── mcp/
│   ├── __init__.py       # 对外导出
│   ├── manager.py        # owner task、连接、调用、关闭和汇总
│   ├── trust.py          # 指纹、信任请求和原子存储
│   └── tool.py           # 动态参数与 MCP 工具包装
├── config.py             # MCP 配置模型、两层合并、插值准备
├── tools/                # 显式 schema 支持和执行器泛化
├── permissions/          # MCP 名称规则与 effect 决策
└── ui/                   # 启动 worker、信任屏幕、清除命令

tests/
├── test_mcp_config.py
├── test_mcp_trust.py
├── test_mcp_tool.py
├── test_mcp_manager.py
└── test_mcp_integration.py
```

## 技术决策

| 决策点 | 选择 | 理由与验证 |
|---|---|---|
| SDK | `mcp>=1.27,<2` | 避免 v2 主版本变化；锁文件与集成测试验证实际 API |
| 生命周期 | 每 server 一个长期 owner task | SDK 上下文在创建 task 内退出；关闭测试检查无悬挂任务 |
| HTTP 客户端 | 每 server 独立、只注入显式 headers | 防止继承全局认证或 Cookie；测试抓取实际请求头 |
| 动态参数 | 显式 schema + 宽松对象模型 | 保留远端 schema 且最小改动现有 Tool 协议 |
| 信任粒度 | 项目和原始配置指纹 | 配置变化自动失效；测试修改每个关键字段都重新询问 |
| 权限规则 | MCP 名称级匹配 | 与既定 Spec 一致；参数级策略留给后续版本 |
| 启动位置 | Textual 初始化 worker | 信任 UI、MCP 会话和工具调用共享同一事件循环 |

## 测试设计

- 配置：两层完整覆盖、局部告警、URL 校验、插值、缺失变量和最小环境。
- 信任：首次询问、相同指纹复用、字段变化失效、拒绝零副作用、原子保存、清除当前项目。
- 工具：schema 透传、注解分类、命名校验、重复处理、文本拼接、错误映射、脱敏和告警去重。
- 权限：精确/通配 allow/deny、四种模式、Plan 可见性、“始终允许”只保存精确名、内置护栏回归。
- Manager：多 server 并发、30 秒边界使用短测试时钟、单点失败、调用取消、断连、5 秒关闭和 owner task 回收。
- 集成：官方 SDK 的测试 server 分别通过 stdio、HTTP JSON 和 HTTP SSE 完成 initialize/list/call；确认 headers 和最小环境。
- UI：初始化期间输入禁用、信任顺序展示、拒绝继续启动、Ready 汇总、退出触发关闭。
- 回归：现有 152 项测试、`ruff format --check`、`ruff check`、完整 pytest 全部通过。

## Spec 覆盖检查

F1-F4 由配置与信任层覆盖；F5-F7、F12-F14 由 manager 和 owner task 覆盖；F8-F10 由动态工具包装覆盖；F11 由权限泛化覆盖；F15 由 Textual 初始化 worker 和启动汇总覆盖。没有遗留实现决策或未分配需求。
