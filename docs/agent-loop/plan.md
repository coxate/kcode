# KCode Agent Loop Plan

## 架构概览

`AgentRunner` 取代固定两请求的 `TurnRunner`，维护轮次、未知工具连续计数、累计用量与停止状态。`StreamAccumulator` 同时转发流增量和形成完整响应。`ToolScheduler` 在循环与 `ToolExecutor` 之间完成安全分类与分批。`AgentSession` 保存模式和一次性计划。Provider 只做协议映射，Conversation 只保存完整检查点，TUI 只消费事件。

## 核心数据结构与接口

- `AgentConfig`：`max_iterations=10`（1–100），`max_parallel_tools=4`（1–16）。
- `AgentMode`：`PLAN`、`DO`；`AgentSession` 保存模式和最新计划。
- `AgentStopReason`：正常完成、迭代上限、取消、未知工具上限、流错误和无效响应。
- `TokenUsage`：可选输入、输出、总量、缓存创建和缓存读取 Token。
- `AgentProgress`：模式、当前轮次、上限、阶段和批次序号。
- `AgentRunner.run(user_text, session)`：返回异步 `AgentEvent`；`cancel()` 发出本次运行的协作取消。
- `ModelResponse`：文本、thinking、多工具、停止原因、Provider 连续状态和用量。
- `ToolEffect`：只读或有副作用；命令在参数校验后动态判定。
- `PreparedToolCall`：保存已校验参数、效果类别或结构化预处理错误。
- `ToolScheduler.execute(...)`：并发只读、串行副作用并稳定输出结果。
- Conversation 提供 begin、checkpoint、complete 和 stop 四个轮次边界。

## 模块交互

1. TUI 创建对话轮次，把用户文本与 Session 模式交给 Runner；Do Mode 原子消费一次计划。
2. Runner 按模式构造系统约束和工具定义，发出轮次进度并收集 Provider 流。
3. 无工具且文本非空时完成；有工具时预处理、分批执行、回灌并进入下一轮。
4. 相邻只读调用由信号量限制并发；副作用调用形成串行屏障；结果按调用索引提交。
5. 整轮全部为未知工具才累加计数；任一已知工具会清零，第二个连续全未知轮次后停止。
6. 第 10 次模型响应仍有工具时完成该工具批次和检查点，但不发起第 11 次请求。
7. 取消或流错保留先前检查点；批次取消时记录真实完成结果和未执行调用的取消结果。
8. `/plan`、`/do` 只切换 Session；`/clear` 清除 Conversation、计划和模式。

## 文件组织

```text
src/kcode/
├── orchestration.py   # AgentRunner、StreamAccumulator 与停止判定
├── session.py         # AgentMode、AgentSession
├── events.py          # ProviderEvent 与 AgentEvent
├── conversation.py    # 历史检查点
├── config.py          # AgentConfig
├── providers/         # 多工具、usage 与连续状态映射
├── tools/
│   ├── base.py        # ToolEffect、PreparedToolCall
│   ├── executor.py    # 预处理和执行
│   └── scheduler.py   # 安全批次调度
└── ui/                # 模式命令、进度、Token 与循环渲染
```

## 技术决策

- 使用 `asyncio.TaskGroup` 与 `Semaphore` 管理并发，预分配结果槽保证顺序。
- 每个模型响应计一次迭代，工具数量不增加轮次。
- Token 缺失字段保持 `None`；累计值不以零替代未知值。
- DeepSeek 工具轮次保存并回传 `reasoning_content`；Anthropic 保留完整 thinking 与签名状态。
- 不可安全中断的原子文件操作完成后记录真实结果；命令和可协作搜索立即取消。
- 计划作为独立系统上下文注入，不能覆盖工具、安全和当前用户请求。
- 版本升级到 0.3.0，不增加第三方运行依赖。
