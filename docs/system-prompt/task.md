# KCode 系统提示工程化 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/kcode/prompting/builder.py` | 模块、装配器和提示包 |
| 新建 | `src/kcode/prompting/sections.py` | 默认模块、优先级和稳定提示内容 |
| 新建 | `src/kcode/prompting/environment.py` | 环境采集、git 探测和渲染 |
| 新建 | `src/kcode/prompting/reminders.py` | Plan/approved-plan reminder |
| 新建 | `src/kcode/prompting/__init__.py` | 提示包公共导出 |
| 修改 | `src/kcode/conversation.py` | 专用系统消息类型 |
| 修改 | `src/kcode/orchestration.py` | AgentRunner 提示生命周期 |
| 修改 | `src/kcode/providers/openai.py` | 显式/自动缓存与用量映射 |
| 修改 | `src/kcode/providers/anthropic.py` | system 内容块与缓存断点 |
| 修改 | `src/kcode/tools/*.py` | 强化六个工具描述 |
| 修改 | `src/kcode/__init__.py` | 版本 0.3.1 |
| 修改 | `pyproject.toml`、`uv.lock` | 版本、Ruff 依赖与配置 |
| 新建/修改 | `tests/*.py` | 单元、集成和回归测试 |
| 新建 | `docs/system-prompt/smoke.md` | 真实缓存与人工对比步骤 |

## T1：扩展请求消息模型

**文件：** `src/kcode/conversation.py`
**依赖：** 无

**步骤：**
1. 定义 `StableSystemMessage`、`EnvironmentMessage`、`SystemReminderMessage`。
2. 实现 reminder 标签转义与 `render()`。
3. 扩展 `ConversationMessage`，保留旧系统消息兼容。

**验证：** 运行 Conversation 定向测试，确认类型可序列化且现有历史测试通过。

## T2：实现模块装配器

**文件：** `src/kcode/prompting/builder.py`
**依赖：** T1

**步骤：**
1. 实现 `PromptSection` 和 `SystemPromptBuilder`。
2. 校验空名称、重复优先级并稳定降序装配。
3. 实现 `PromptPackage.messages()` 固定顺序。

**验证：** 运行装配器测试，确认排序、空槽、异常和字节稳定性。

## T3：定义十个提示模块

**文件：** `src/kcode/prompting/sections.py`
**依赖：** T2

**步骤：**
1. 定义十个固定优先级。
2. 写入七个英文模块内容和三个空槽。
3. 在动作与工具模块写入专用工具优先和编辑前先读规则。

**验证：** 运行默认模块测试，确认名称、顺序、标题和关键文本。

## T4：实现 reminder 构造器

**文件：** `src/kcode/prompting/reminders.py`
**依赖：** T1

**步骤：**
1. 定义完整和精简 Plan 内容。
2. 按第 1 次及每 5 次选择完整版。
3. 实现 approved-plan reminder 和空计划跳过。

**验证：** 测试迭代 1、2、5、10、15、非法迭代和冲突标签。

## T5：实现环境文本渲染

**文件：** `src/kcode/prompting/environment.py`
**依赖：** T1

**步骤：**
1. 定义 `EnvironmentSnapshot` 和固定字段顺序。
2. 实现 XML 文本转义。
3. 格式化平台、日期、版本、模型和 git 状态。

**验证：** 使用固定输入断言完整环境块及特殊字符转义。

## T6：实现有界 git 探测

**文件：** `src/kcode/prompting/environment.py`
**依赖：** T5

**步骤：**
1. 使用异步无 shell 子进程执行 porcelain status。
2. 加入 0.5 秒超时、64 KiB 上限和进程回收。
3. 解析 branch/detached 与 clean/dirty，并实现所有降级值。

**验证：** 测试 clean、dirty、detached、非仓库、缺失、超时、超限和无效输出。

## T7：导出 prompting 公共接口

**文件：** `src/kcode/prompting/__init__.py`
**依赖：** T2、T3、T4、T6

**步骤：** 导出装配器、默认模块、环境收集器、提示包和 reminder 构造器。

**验证：** 运行导入测试，确认无循环依赖。

## T8：集成 AgentRunner 初始化与环境生命周期

**文件：** `src/kcode/orchestration.py`
**依赖：** T7

**步骤：**
1. 注入可替换的 builder 与 collector。
2. 初始化时构造一次稳定提示。
3. 每个 Agent 任务只采集一次环境。

**验证：** 模拟多轮工具循环，断言环境只采集一次、稳定提示不变。

## T9：集成 Plan 与 approved-plan reminder

**文件：** `src/kcode/orchestration.py`
**依赖：** T8

**步骤：**
1. 每次模型迭代构造当前 reminder。
2. 固定请求顺序为提示包、历史、当前消息。
3. approved plan 在本任务所有迭代注入，下一任务消失。
4. 删除旧的固定系统提示拼装路径。

**验证：** 扩展 Agent Loop 测试覆盖 10 轮节奏、Plan 工具集合和一次性计划。

## T10：重构 Anthropic system 序列化

**文件：** `src/kcode/providers/anthropic.py`
**依赖：** T1

**步骤：**
1. 保留各 system 内容块，不再连接成单字符串。
2. 仅给稳定块设置 `cache_control={"type":"ephemeral"}`。
3. 保持工具、thinking、tool use/result 和普通消息往返。

**验证：** Provider 测试断言唯一断点、动态块无标记及既有流事件不变。

## T11：实现 OpenAI 缓存能力识别

**文件：** `src/kcode/providers/openai.py`
**依赖：** T1

**步骤：**
1. 定义 `OpenAICacheMode` 和已知模型前缀常量。
2. 严格解析 base URL 主机并识别官方显式模式。
3. 确保 DeepSeek、自定义端点和旧模型进入自动模式。

**验证：** 参数化测试官方新模型、旧模型、自定义端点和 DeepSeek。

## T12：实现稳定 cache key

**文件：** `src/kcode/providers/openai.py`
**依赖：** T11

**步骤：**
1. 规范化工具定义 JSON。
2. 对版本、模型、稳定提示和工具摘要计算 SHA-256。
3. 输出固定 `kcode:v1:` 格式。

**验证：** 测试稳定输入同 key，稳定内容变化换 key，动态内容不参与。

## T13：映射 OpenAI 显式与自动请求

**文件：** `src/kcode/providers/openai.py`
**依赖：** T12

**步骤：**
1. 显式模式为稳定 system 内容块添加 breakpoint。
2. 设置 stable key 和 explicit policy。
3. 自动模式保持字符串 content 且不发送显式字段。
4. 保持工具调用和 DeepSeek reasoning 往返。

**验证：** 检查两个模式的完整 SDK 请求字典和消息顺序。

## T14：统一缓存用量归一化

**文件：** `src/kcode/providers/openai.py`、`src/kcode/providers/anthropic.py`
**依赖：** T10、T13

**步骤：**
1. 实现可选非负整数读取。
2. 映射 Anthropic、OpenAI creation/read 和 DeepSeek hit。
3. 保留零，拒绝负数、布尔值和异常类型；忽略 DeepSeek miss 创建映射。

**验证：** 参数化测试正数、零、缺失、空值、负数和异常类型。

## T15：强化六个工具描述

**文件：** `src/kcode/tools/filesystem.py`、`search.py`、`command.py`
**依赖：** T3

**步骤：** 更新描述但不修改名称、schema、效果分类或执行逻辑。

**验证：** 测试系统提示与工具描述均包含两条关键规则，原工具测试通过。

## T16：升级版本并引入 Ruff

**文件：** `src/kcode/__init__.py`、`pyproject.toml`、`uv.lock`
**依赖：** 无

**步骤：**
1. 将应用和包版本改为 0.3.1。
2. 增加 Ruff 开发依赖和 Python 3.11、行宽 100、`E/F/I` 配置。
3. 更新锁文件。

**验证：** 检查包版本、banner 和 Ruff 命令可用。

## T17：建立全项目 Ruff 基线

**文件：** `src/**/*.py`、`tests/**/*.py`
**依赖：** T16

**步骤：**
1. 对 `src`、`tests` 执行 Ruff 格式化。
2. 修复 lint 告警，不改变既有行为。
3. 将机械格式变化与功能修复分组检查。

**验证：** `ruff format --check src tests` 与 `ruff check src tests` 均通过。

## T18：完成提示与环境测试

**文件：** `tests/test_prompting.py`、`tests/test_environment.py`
**依赖：** T7

**步骤：** 补齐 T2–T7 的单元测试和安全/降级覆盖。

**验证：** 两个测试文件独立通过。

## T19：完成 Provider 与 Agent 集成测试

**文件：** `tests/test_agent_loop.py`、`test_openai_provider.py`、`test_anthropic_provider.py`、`test_conversation.py`、`test_tools.py`
**依赖：** T9、T14、T15

**步骤：** 补齐请求顺序、历史隔离、缓存模式、用量字段及既有行为回归。

**验证：** 相关测试文件全部通过且无网络调用。

## T20：编写人工 smoke 与定性对比说明

**文件：** `docs/system-prompt/smoke.md`
**依赖：** T14

**步骤：**
1. 记录三 Provider 串行重复请求及 UsageReported 观察方法。
2. 说明缓存阈值、TTL、费用、未知字段和等待要求。
3. 定义五个变更前后人工对比场景和记录表。

**验证：** 文档步骤不依赖默认测试，不包含真实密钥或自动联网脚本。

## T21：全量回归

**文件：** 全项目
**依赖：** T17、T18、T19、T20

**步骤：** 运行全量 pytest、Ruff 格式检查和 lint，复核 git diff 不含用户的 `hello.txt`。

**验证：** 所有命令退出码为 0，默认测试不访问网络。

## 执行顺序

```text
T1 → T2 → T3 ─┬→ T4 → T7 → T8 → T9 ───────────┐
               ├→ T5 → T6 ────────┘             │
               └→ T15                            │
T1 → T10 ───────────────┐                        ├→ T19 ─┐
T1 → T11 → T12 → T13 ───┴→ T14 ─────────────────┘       │
T16 → T17 ────────────────────────────────────────────────┤
T7 → T18 ─────────────────────────────────────────────────┤
T14 → T20 ────────────────────────────────────────────────┤
                                                         └→ T21
```
