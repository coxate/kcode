# Kcode MCP 客户端：面试素材记录

## 模块目标与架构

Kcode v0.5.0 从六个编译期内置工具扩展为配置驱动的 MCP 工具生态。数据流分为配置解析、项目级启动信任、秘密展开、并发连接、工具适配、权限判断和 TUI 生命周期七层。每个 server 使用独立 owner task 持有 SDK 会话。

## 为什么选择当前方案

- 使用官方 MCP Python SDK，不自研 JSON-RPC，降低协议兼容风险。
- 项目级 stdio 与 HTTP 都先做配置指纹信任，防止打开项目即执行命令或发送秘密。
- stdio 采用最小环境继承，只有显式 env 能获得其他宿主变量。
- 动态 Schema 通过 ToolSpec 显式字段接入，Provider 不需要 MCP 分支。
- 缺失 readOnlyHint 按有副作用处理，用额外确认换取更低的不可逆风险。

## 重要错误与排查

### stdio errlog 缺少文件描述符

初版脱敏 writer 只有 `write()`，但 SDK 创建子进程时需要操作系统文件描述符，导致 server 在 initialize 前失败。通过启动汇总定位到 AttributeError。当前安全修正是丢弃原始 server stderr，只展示 Kcode 结构化错误；实时脱敏 stderr 管道留待后续。

### HTTP 集成测试无法监听端口

失败发生在测试 bind 本机端口阶段，尚未进入 MCP 代码。普通沙箱中按条件跳过；获得仅本机回环权限后 JSON/SSE 测试均通过。这说明错误属于测试环境权限，而不是协议实现。

### 合法空 Schema 被真假判断误伤

Python 中空字典为假值，使用 `explicit_schema or generated_schema` 会错误替换合法 `{}`。改为显式判断 `is not None`，保留远端 Schema 语义。

## 性能、安全与部署取舍

- server 并发启动，单个 30 秒超时，避免启动时间随 server 数量线性增长。
- 只读工具沿用 Kcode 并发上限；副作用工具保守串行。
- HTTP 每 server 独立 client，不共享 Cookie、认证和系统代理环境。
- 关闭总预算 5 秒；卡住的 owner task 会取消回收，避免终端退出挂死。
- 信任文件仅保存项目哈希和配置指纹，使用原子替换与 0600 权限。

## 放弃或延期的方案

- 放弃“项目配置自动启动”：无法防止恶意仓库在工具授权前执行代码。
- 放弃完整环境继承：兼容性更强，但会把无关 Token 暴露给子进程。
- 放弃直接透传 stderr：未经中转脱敏可能泄漏凭据。
- 延期 OAuth、自动重连、热加载、非文本内容回灌和参数级权限规则。

## 面试官可能追问

- 为什么启动信任和工具授权必须分两层？
- 配置指纹为什么不能包含展开后的 Token？
- owner task 与普通 asyncio task 有什么区别？
- 连接超时后怎样保证其他 server 不受影响？
- readOnlyHint 如果被 server 谎报怎么办？
- 为什么动态 JSON Schema 不直接转换成静态 Pydantic 模型？
- 如何进一步实现实时 stderr 脱敏、OAuth 和自动重连？

## 学习归属

- 用户已亲自理解：HTTP URL 变化会使配置指纹失效，必须重新确认。
- 用户参与决定：项目级统一信任、配置指纹、分层容错、最小环境继承和缺失变量隔离。
- Codex 协助完成：当前代码实现、测试设计、SDK 生命周期接入和错误修复。
- 用户仍需通过复述或亲自修改证明：启动信任、owner task、readOnlyHint 安全默认和完整实现细节。
