# KCode 系统提示工程化 Checklist

> 每项都通过运行代码、检查请求数据或观察实际行为验证。先记录证据，再标记通过。

## 实现完整性

- [x] 十个提示模块名称与优先级唯一，七个固定模块顺序正确，三个空槽不产生多余内容。（提示单元测试）
- [x] 稳定提示逐字节确定，且系统提示与工具描述均包含专用工具优先和编辑前先读规则。（提示与工具测试）
- [x] 三种专用系统消息及 PromptPackage 正确构造，reminder 标签包裹和冲突转义正确。（消息测试）
- [x] 环境块含目录、平台、日期、git、版本、模型；动态值正确转义。（环境测试）
- [x] git clean/dirty/detached、非仓库、缺失、超时、超限和无效输出均正确处理，且不泄漏文件名。（环境测试）
- [x] 环境每个 Agent 任务只采集一次；Plan reminder 在 1/5/10/15 轮完整，其余精简。（Agent 集成测试）
- [x] `/do` 一次性消费计划，同一任务所有迭代使用相同 approved-plan reminder。（Agent 集成测试）
- [x] 注入消息不进入持久历史，不破坏工具调用和结果配对。（Conversation/Agent 测试）
- [x] 包版本、应用版本及 banner 均为 0.3.1。（版本与 UI 测试）

## Provider 与缓存

- [x] Anthropic system 使用内容块数组，只有稳定块带唯一 5 分钟 cache_control。（Provider 测试）
- [x] OpenAI 官方 `gpt-5.6*` 使用 breakpoint、稳定 key 和 explicit policy。（Provider 测试）
- [x] 旧 OpenAI、自定义端点及 DeepSeek 使用自动模式且无显式字段。（Provider 测试）
- [x] cache key 只受模型、稳定提示和规范化工具定义影响。（Provider 测试）
- [x] Anthropic creation/read、OpenAI write/read、DeepSeek hit 正确映射；合法零保留，非法或缺失值为未知。（用量测试）
- [x] thinking、reasoning、多工具流和 continuation state 不退化。（Provider 回归测试）

## 安全、集成与回归

- [x] 请求顺序固定为稳定提示、环境、reminder、历史、当前消息。（Agent 请求快照）
- [x] 环境、错误、调试输出和 TUI 不包含 API Key 或敏感环境变量。（安全测试）
- [x] 多轮 Agent、并发调度、副作用串行、授权、取消、停止条件及检查点保持正确。（全量测试）
- [x] TUI、配置合并、Provider 工厂和六工具既有行为不退化。（全量测试）
- [x] 用户未跟踪文件 `hello.txt` 未被修改或纳入实现。（最终 git diff/status）

## 编译、格式与测试

- [x] `uv run python -c "import kcode; import kcode.prompting"` 通过。
- [x] `uv run pytest` 全部通过，默认测试无真实网络和模型费用。
- [x] `uv run ruff format --check src tests` 通过。
- [x] `uv run ruff check src tests` 通过。

## 端到端场景

- [ ] 临时仓库中完成搜索、读取、编辑、验证和最终总结，专用工具顺序正确。
- [ ] Plan Mode 至少十轮保持只读并按频率注入；切换 Do 后一次性按计划执行。
- [ ] git 不可用或超时时环境降级且普通请求正常完成。
- [ ] 工具调用后取消，再次提问时历史合法且 Provider 可继续响应。

## 真实缓存 Smoke（需用户明确提供真实配置并接受费用）

- [ ] Anthropic 首次 creation > 0，TTL 内后续 read > 0。
- [ ] OpenAI 显式模型首次 write > 0，后续 cached > 0。
- [ ] DeepSeek 后续 prompt cache hit > 0。
- [ ] 不支持缓存或未达阈值时报告零/未知而不报错，输出不泄漏密钥。

## 人工定性对比

- [ ] 对比专用搜索工具优先、编辑前读取、Plan 持续只读、环境利用和最终回答质量。
- [ ] 只记录实际工具序列和回答，不建立自动评分框架。
