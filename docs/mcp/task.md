# Kcode MCP 客户端 Tasks

> 状态：已实施并通过自动化验证
> 前置文档：`spec.md`、`plan.md`

## 学习提示

任务按“配置与信任 → 工具与权限抽象 → MCP 连接 → TUI 生命周期 → 端到端验收”推进。先稳定数据和安全边界，再接外部 I/O，可以把错误快速定位到配置、权限、协议或界面层。

## 文件清单

| 操作 | 文件或目录 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml`、`uv.lock`、`src/kcode/__init__.py` | MCP SDK 依赖与版本 |
| 修改 | `src/kcode/config.py` | MCP 配置、两层合并和局部告警 |
| 新建 | `src/kcode/mcp/` | 信任、连接管理和工具包装 |
| 修改 | `src/kcode/tools/` | 动态 JSON Schema 与外部工具执行 |
| 修改 | `src/kcode/permissions/` | MCP 名称规则和副作用分类 |
| 修改 | `src/kcode/orchestration.py` | Plan Mode 工具过滤 |
| 修改 | `src/kcode/ui/` | 信任弹窗、启动状态和清除命令 |
| 修改 | `src/kcode/cli.py` | MCP 配置、manager 和 trust store 装配 |
| 修改 | `config.example.yaml` | 安全配置示例 |
| 新建 | `tests/test_mcp_*.py` | MCP 单元与集成测试 |

## T1：记录干净基线

**依赖：** 无

1. 保留用户已有的 `hello.txt` 和其他无关改动。
2. 运行现有测试、Ruff 检查和格式检查。
3. 记录测试数量与已有失败，不修改源码来掩盖基线问题。

**验证：** 全量测试为当前基线的 152 项通过，Ruff 无错误。

## T2：加入 SDK 和功能版本

**文件：** `pyproject.toml`、`uv.lock`、`src/kcode/__init__.py`
**依赖：** T1

1. 添加 `mcp>=1.27,<2`。
2. 更新锁文件并确认未解析到 MCP v2。
3. 将项目版本和 `__version__` 升为 `0.5.0`。

**验证：** 在项目环境中可导入 `mcp`，Kcode 显示 `0.5.0`。

## T3：定义 MCP 配置模型

**文件：** `src/kcode/config.py`、`tests/test_mcp_config.py`
**依赖：** T1

1. 定义严格的 stdio/http 配置模型和 `user/project` 来源。
2. 校验 command、args、env、URL 与 headers。
3. 拒绝非 HTTP(S) URL 和含用户信息的 URL。
4. 添加合法与非法字段的参数化测试。

**验证：** 非法 server 产生局部告警，不抛核心配置异常。

## T4：实现两层 MCP 合并

**文件：** `src/kcode/config.py`、`tests/test_mcp_config.py`
**依赖：** T3

1. 从两个现有配置文件读取 `mcp_servers`。
2. 用户级先加载，项目级同名 server 完整覆盖。
3. MCP 段错误时忽略该段并告警。
4. 保持 Provider 现有字段级合并。
5. 将 MCP 结果和告警加入 `AppConfig`。

**验证：** Provider 既有测试通过；MCP 同名 server 不发生字段混合。

## T5：实现变量扫描与插值

**文件：** `src/kcode/config.py`、`tests/test_mcp_config.py`
**依赖：** T3

1. 支持完整值、字符串内及多个 `${VAR}`。
2. 分离“只扫描变量名”和“实际读取并展开”两个阶段。
3. 缺少变量时返回 server 级错误。
4. 收集所有展开后的非空敏感值。
5. command、args 和 URL 不做展开。

**验证：** 覆盖完整值、Bearer、多变量、缺失变量、不应展开的字段和错误脱敏。

## T6：定义信任请求和配置指纹

**文件：** `src/kcode/mcp/trust.py`、`tests/test_mcp_trust.py`
**依赖：** T3

1. 定义只含安全展示信息的信任请求。
2. 将项目路径、server 名和原始配置规范化为稳定 JSON。
3. 计算 SHA-256 指纹。
4. 测试关键字段变化使指纹失效。

**验证：** 相同配置指纹稳定；项目、command、args、URL、env 或 headers 变化得到不同指纹；不含展开值。

## T7：实现原子信任存储

**文件：** `src/kcode/mcp/trust.py`、`tests/test_mcp_trust.py`
**依赖：** T6

1. 实现带版本号的 `~/.kcode/mcp-trust.json`。
2. 使用临时文件、flush/fsync、原子替换和 `0600` 权限。
3. 实现检查、记录和按当前项目清除。
4. 损坏存储按“不信任”处理并告警。
5. 用进程内锁保护并发访问。

**验证：** 覆盖不存在、重复记录、损坏、写入失败、权限和项目清除；文件不保存配置或秘密正文。

## T8：扩展工具定义以支持动态 Schema

**文件：** `src/kcode/tools/base.py`、`registry.py`、`tests/test_tools.py`
**依赖：** T1

1. 为 `ToolSpec` 增加可选显式参数 Schema。
2. Registry 优先使用显式 Schema，否则继续由 Pydantic 生成。
3. 增加允许任意 JSON 对象字段的 MCP 参数模型。
4. 保证六个内置工具定义不变。

**验证：** 远端 Schema 原样进入 Provider 工具定义；内置工具测试通过。

## T9：实现 MCP 工具结果适配

**文件：** `src/kcode/mcp/tool.py`、`tests/test_mcp_tool.py`
**依赖：** T8

1. 定义包含完整名、远端名、Schema、effect 和 server handle 的 `McpTool`。
2. 按顺序拼接文本块并保留空文本成功。
3. 映射远端错误和传输异常。
4. 对非文本块产生可去重告警。
5. 保证结果继续经过现有脱敏链路。

**验证：** 覆盖多文本、空文本、远端错误、异常、告警去重和秘密替换。

## T10：泛化权限规则名称

**文件：** `src/kcode/permissions/models.py`、`rules.py`、权限测试
**依赖：** T1

1. 将固定友好名泛化为字符串。
2. 保留六个内置规则语法。
3. 允许合法 MCP 精确名和带 `*` 的名称模式。
4. MCP 规则只匹配工具名，不解析参数。
5. 继续拒绝其他未知格式。

**验证：** 精确匹配、server 通配、跨 server 隔离、deny 优先和内置规则回归通过。

## T11：增加外部工具权限目标

**文件：** `src/kcode/permissions/commands.py`、`engine.py`、权限测试
**依赖：** T8、T10

1. 内置工具继续生成现有命令或路径目标。
2. MCP 工具生成“完整名 + 空匹配值 + effect”目标。
3. 黑名单和沙箱只作用于原有类别。
4. 根据 effect 与权限模式计算 verdict。
5. Plan Mode 拒绝有副作用 MCP 工具。

**验证：** 四种权限模式以及危险命令、沙箱回归全部通过。

## T12：让执行器使用通用权限决策

**文件：** `src/kcode/tools/executor.py`、`tests/test_tool_executor.py`
**依赖：** T11

1. 移除“所有未知类别工具一律询问”的冲突路径。
2. 所有已注册工具统一进入 PermissionEngine。
3. 只读 MCP 无规则时直接执行。
4. 有副作用 MCP 生成脱敏审批请求。
5. 永久允许只保存完整工具名；bypass 不弹窗。

**验证：** 覆盖只读、副作用、拒绝、永久允许、通配规则和 bypass。

## T13：更新 Plan Mode 工具过滤

**文件：** `src/kcode/orchestration.py`、Agent Loop 测试
**依赖：** T8、T11

1. 保留 Plan Mode 的四个内置工具。
2. 动态加入只读 MCP 工具。
3. 不向 Provider 发送有副作用 MCP 定义。
4. 直接伪造副作用 MCP call 时仍拒绝。

**验证：** Anthropic/OpenAI 假 Provider 收到正确的 Plan 工具集合。

## T14：建立 Manager 公共模型

**文件：** `src/kcode/mcp/manager.py`、`__init__.py`、`tests/test_mcp_manager.py`
**依赖：** T5、T6、T9

1. 定义 server 状态、启动结果和汇总。
2. 定义 handle 的 session 状态与调用入口。
3. 定义 ready、failed 和 stop 信号。
4. 允许测试注入 transport factory 和短超时。
5. 异常只保留安全摘要。

**验证：** 使用假 transport/session 验证状态转换，无真实网络或进程。

## T15：实现顺序信任和安全解析

**文件：** `src/kcode/mcp/manager.py`、`tests/test_mcp_manager.py`
**依赖：** T5、T7、T14

1. 用户级 server 直接解析，项目级 server 逐个请求信任。
2. 被拒绝 server 不读取环境值。
3. 信任后才展开变量并构造最小 env 或 headers。
4. 缺失变量只跳过当前 server。
5. 合并 Provider Key 与 MCP 敏感值。

**验证：** 拒绝前没有环境读取、传输创建、进程或网络副作用。

## T16：实现 Owner Task 生命周期

**文件：** `src/kcode/mcp/manager.py`、`tests/test_mcp_manager.py`
**依赖：** T14、T15

1. 每个 server 创建长期 owner task。
2. 在 owner task 内进入 transport 和 ClientSession。
3. initialize/list_tools 后报告 ready。
4. stop 后在同一 task 内退出上下文。
5. 启动限制 30 秒，整体关闭限制 5 秒。
6. 取消并回收超时残留任务。

**验证：** 上下文进入/退出 task ID 相同，启动/关闭超时有效，无 pending task。

## T17：接入 stdio Transport

**文件：** `src/kcode/mcp/manager.py`、`tests/test_mcp_integration.py`
**依赖：** T2、T16

1. 使用 SDK `StdioServerParameters` 和 `stdio_client`。
2. 传入 command、args 和最小环境。
3. 安全化 stderr 诊断。
4. 建立测试 stdio MCP server。

**验证：** 真实完成 initialize/list/call；显式变量可见、未声明 Token 不可见，退出后无子进程。

## T18：接入 Streamable HTTP

**文件：** `src/kcode/mcp/manager.py`、`tests/test_mcp_integration.py`
**依赖：** T2、T16

1. 每 server 创建独立 HTTP client。
2. 只注入配置 headers，不共享 Cookie 或认证。
3. 使用 SDK `streamable_http_client`。
4. 覆盖 JSON 与 SSE 响应并正确关闭客户端。

**验证：** 测试 server 收到预期 header；JSON/SSE 均完成 initialize/list/call。

## T19：实现工具发现和注册汇总

**文件：** `src/kcode/mcp/manager.py`、`tool.py`、manager 测试
**依赖：** T9、T16

1. 构造和校验完整工具名。
2. 同 server 重名保留第一个。
3. 不覆盖 Registry 已有工具。
4. 将 readOnlyHint 转成 effect。
5. 返回工具、server 状态、数量和安全告警。

**验证：** 覆盖合法、非法、重复、跨 server 同名和注解缺失。

## T20：增加 MCP 信任弹窗

**文件：** `src/kcode/ui/mcp_trust.py`、UI 测试
**依赖：** T6

1. 显示项目、server、命令或 URL 摘要和变量名。
2. 不显示环境值。
3. 提供“信任当前配置”和“拒绝”。
4. Escape 等同拒绝，默认焦点采用安全选项。

**验证：** Textual headless 测试覆盖展示、键盘、取消和秘密不渲染。

## T21：接入启动生命周期

**文件：** `src/kcode/ui/app.py`、`src/kcode/cli.py`、UI 测试
**依赖：** T15、T19、T20

1. CLI 创建 manager/trust store 并传给 App。
2. on_mount 启动独占初始化 worker。
3. 初始化期间禁用输入。
4. 顺序信任后并发连接。
5. 一次性注册工具、更新敏感值并展示汇总。
6. 无 MCP 时保持原启动体验。

**验证：** 未 Ready 不能发送消息；拒绝和单点失败不阻断启动；Ready 后工具集合稳定。

## T22：接入关闭生命周期

**文件：** `src/kcode/ui/app.py`、UI/manager 测试
**依赖：** T16、T21

1. App 卸载时 shield manager close。
2. 最多等待 5 秒。
3. 清理期间拒绝新调用。
4. 未配置或尚未启动时安全返回。

**验证：** `/exit`、Ctrl+C、初始化失败和正常退出都回收任务、客户端和子进程。

## T23：实现信任清除命令

**文件：** `src/kcode/ui/commands.py`、`app.py`、命令测试
**依赖：** T7、T21

1. 解析 `/mcp trust clear`。
2. 清除当前项目信任。
3. 提示重启后重新确认。
4. 存储失败时显示可操作错误。
5. 更新 `/help`。

**验证：** 覆盖大小写、空格、未知子命令、成功和失败。

## T24：更新示例和安全说明

**文件：** `config.example.yaml`、MCP 文档
**依赖：** T3、T5

1. 增加 stdio 和 HTTP 示例。
2. 秘密只使用 `${VAR}`。
3. 说明首次信任、最小环境和清除命令。

**验证：** 配置加载器能解析示例；仓库无示例 Token 明文。

## T25：跨 Provider 回归

**文件：** Provider 和 Agent Loop 测试
**依赖：** T12、T13、T19

1. 将同一 MCP 定义交给 Anthropic/OpenAI。
2. 比较名称、描述、Schema 和 ToolResult 回灌。
3. 确认 Provider 源码没有 MCP 来源分支。

**验证：** 两套 Provider 测试与 Agent Loop 测试通过。

## T26：完成全量质量门槛

**依赖：** T2–T25

1. 运行 MCP 专项和全量测试。
2. 运行 Ruff 格式与静态检查。
3. 检查差异、明文秘密、悬挂任务和残留子进程。
4. 手动运行一次 stdio/HTTP 冒烟测试。
5. 对照 `checklist.md` 记录结果。

**验证：** 所有测试和检查通过，无凭据、任务或进程泄漏。

## 执行顺序

```text
T1
├── T2
├── T3 → T4 → T5
├── T6 → T7
└── T8 → T9

T10 → T11 → T12 → T13

T5 + T7 + T9 → T14 → T15 → T16
                              ├── T17
                              ├── T18
                              └── T19

T6 → T20
T15 + T19 + T20 → T21 → T22 → T23
T3 + T5 → T24
T12 + T13 + T19 → T25
全部任务 → T26
```

## 学习与实施归属

- 用户已参与并批准安全边界、信任策略、总体架构和任务拆分。
- 实施时最值得用户逐行理解：T5 环境变量边界、T11 权限分类、T16 owner task 生命周期。
- Codex 可以协助实现和测试；在用户能解释目的、失败模式和验证方法前，不表述为用户独立掌握。
