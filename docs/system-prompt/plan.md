# KCode 系统提示工程化 Plan

## 架构概览

新增 `kcode.prompting` 包，集中负责稳定提示模块、环境上下文和动态 reminder。`AgentRunner` 组装提示包与会话历史，Provider 只处理协议映射和缓存字段，Conversation 不持久化任何注入消息。

请求顺序固定为：稳定系统消息 → 环境消息 → reminder → 历史 → 当前用户/工具消息。

## 核心数据结构

- `PromptSection(name, priority, content)`：不可变提示模块。
- `SystemPromptBuilder(sections).build()`：校验名称和唯一优先级，过滤空内容，按优先级降序用双换行连接。
- `StableSystemMessage`：唯一可缓存系统文本。
- `EnvironmentMessage`：每个 Agent 任务采集一次的动态环境块。
- `SystemReminderMessage(kind, content).render()`：带标签且不持久化的动态指令。
- `PromptPackage(stable, environment, reminders).messages()`：固定返回三类提示消息。
- `EnvironmentSnapshot` 与 `EnvironmentCollector.collect()`：环境采集及固定格式渲染。
- `OpenAICacheMode`：`EXPLICIT` 或 `AUTOMATIC`。

固定优先级为 1000/900/800/700/600/500/400/300/200/100，依次对应身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出、自定义指令、已激活 Skill、长期记忆。

## 模块设计

稳定提示使用英文，覆盖 KCode 身份、安全约束、Do/Plan 一般语义、先调查后修改、编辑前先读、专用工具优先、验证要求、协作语气和结果优先输出。三个预留模块内容为空。

环境块使用 `<environment_context>`，字段固定为绝对工作目录、系统与架构、本地日期、git 分支与 clean/dirty、KCode 版本和模型。git 通过无 shell 异步子进程执行 `git status --porcelain=v1 --branch --untracked-files=no`，超时 0.5 秒、输出上限 64 KiB，不输出文件名。

Plan reminder 在第 1 次和每个 5 的倍数使用完整版，其他迭代使用精简版。approved-plan reminder 在一次 Do 任务的所有模型迭代中保持一致。冲突的 reminder 标签会被转义。

## Provider 映射

Anthropic 使用 system 内容块数组，只在稳定 system 块设置 `cache_control={"type":"ephemeral"}`；按其 `tools → system → messages` 顺序覆盖稳定工具和提示，不缓存动态块或历史。

OpenAI 仅在主机为 `api.openai.com` 且模型匹配已知显式系列（初始 `gpt-5.6*`）时使用显式模式：稳定 system 内容块设置 breakpoint，请求设置 `prompt_cache_options.mode=explicit` 和稳定 cache key。旧模型、兼容端点和 DeepSeek 使用自动模式，不发送显式字段。

cache key 为 `kcode:v1:{sha256 前 32 位}`，摘要输入是模型、稳定提示和规范化工具 JSON。

用量映射：Anthropic creation/read；OpenAI `cache_write_tokens`/`cached_tokens`；DeepSeek hit 只映射读取。合法零保留，缺失、负数、布尔值或异常类型变为 `None`。

## 文件组织

新增 `src/kcode/prompting/{__init__,builder,sections,environment,reminders}.py`，并修改 conversation、orchestration、两个 Provider、六个工具描述、版本和项目配置。新增提示与环境测试，扩展 Agent、Provider、Conversation 和工具测试。阶段文档位于 `docs/system-prompt`。

## 技术决策

- 环境每个 Agent 任务采集一次；只有 Plan reminder 随迭代变化。
- 兼容 `SystemMessage` 和 `ChatMessage(role="system")`，但新主路径只用专用类型。
- 不以网络探测缓存能力，不自动重试，不填充无意义提示以达到缓存阈值。
- 版本升级至 0.3.1。
- 加入 Ruff 开发依赖，Python 3.11、行宽 100、`E/F/I`，对 `src` 和 `tests` 建立全项目基线。
- Mypy 不新增、不作为阻断项。

## 测试计划

覆盖模块排序与稳定性、环境成功与降级、reminder 频率与转义、历史隔离、AgentRunner 集成、三类 Provider 缓存映射、cache key 稳定性、异常用量字段、既有行为回归、版本展示、pytest 与 Ruff。`smoke.md` 记录三 Provider 的真实缓存验证和变更前后人工定性对比，不新增联网脚本或 TUI 展示。
