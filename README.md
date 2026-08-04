# KCode

KCode 是一个 Python 全屏终端 AI 编程助手。它提供流式 Markdown、多轮会话、ReAct Agent Loop、Plan Mode、Claude extended thinking，以及 Anthropic、OpenAI 和 DeepSeek 三种配置方式。

0.3.0 内置六个工具：读取文件、新建文件、唯一匹配修改文件、执行命令、按 glob 查找文件和按正则搜索代码。模型可以在一次请求中连续调用多轮工具；相邻只读工具有界并发，有副作用的工具按顺序串行。

每次请求默认最多运行 10 个模型轮次。模型正常完成、达到迭代上限、用户取消、连续请求未知工具或模型流出错时，界面都会显示明确的停止原因。

## 工具安全

- 相对路径以启动 KCode 的目录为基准，读取工具可以访问整个文件系统。
- 工作区外写入或修改必须由用户单次确认。
- `write_file` 只创建新文件，已有文件必须使用唯一匹配的 `edit_file`。
- 只有严格只读白名单命令免确认，其他命令会弹出授权框。
- 工具输出有超时和大小限制；加载的 API Key 会从结果与错误中脱敏。

## 环境与安装

- Python 3.11 或更高版本
- 推荐使用 [uv](https://docs.astral.sh/uv/)

```bash
uv sync --extra dev
```

## 配置

复制 `config.example.yaml` 到以下任一位置：

- 用户级：`~/.kcode/config.yaml`
- 项目级：`<当前工作目录>/.kcode/config.yaml`

两个文件可以同时存在。项目配置优先，并按 Provider 名逐字段覆盖用户配置。建议把密钥放进环境变量：

```bash
export OPENAI_API_KEY="your-key"
```

配置示例：

```yaml
active_provider: deepseek
agent:
  max_iterations: 10
  max_parallel_tools: 4
providers:
  - name: deepseek
    protocol: openai
    model: deepseek-v4-flash
    base_url: https://api.deepseek.com
    api_key: ${DEEPSEEK_API_KEY}
    thinking: false
```

DeepSeek 使用 OpenAI 兼容协议，因此 `protocol` 保持为 `openai`。Claude 的 `thinking: true` 会开启独立 thinking 区域；该设置对 OpenAI/DeepSeek 会被忽略并显示提示。

## 启动

```bash
uv run kcode
```

也可以：

```bash
uv run python -m kcode
```

命令：`/plan`、`/do`、`/help`、`/clear`、`/exit`。模型生成时按 Ctrl+C 只取消当前任务；空闲时按 Ctrl+C 退出。

## Agent Loop 与 Plan Mode

- 普通启动处于 Do Mode，开放全部六个工具并沿用安全确认。
- 输入 `/plan` 后进入只读规划模式。模型只能读取、查找、搜索，以及执行严格白名单内的只读命令。
- 在 Plan Mode 中输入任务，KCode 会自主调查并保存最终计划。
- 输入 `/do` 切回执行模式。保存的计划只注入下一条普通请求一次，随后自动清除。
- 输入 `/clear` 会同时清除对话、计划并恢复 Do Mode。

底部状态栏显示当前模式、模型轮次和本次请求累计 Token。供应商没有返回用量或流提前中断时显示 `Token ?`，不会把未知用量误记为零。

## 六个工具的界面验收

基础工具验收建议在临时目录启动 KCode，并依次输入：

1. `只调用 write_file，新建 acceptance-note.txt，内容为：KCode write passed`
2. `只调用 read_file，读取 acceptance-note.txt 的第 1 到 20 行，然后概括内容`
3. `只调用 edit_file，把 acceptance-note.txt 中唯一的 KCode write passed 替换为 KCode edit passed`
4. `只调用 run_command，在当前目录执行 pwd，并解释退出码和输出`
5. `只调用 find_files，在当前目录查找 *.txt`
6. `只调用 search_code，在当前目录搜索 KCode edit passed，文件模式为 *.txt`

工具卡片使用黄色边框与标题，表示本地工具动作，而不是模型的第二条回复；模型回复使用蓝色边框并标出迭代轮次。卡片会列出中文参数摘要，并以绿色成功状态或红色失败状态说明实际执行结果。`write_file` 成功时会明确显示目标文件和写入字节数。

## API Key 是否需要购买

KCode 本身不提供模型额度。调用哪个供应商，就需要该供应商可用的 API Key 和账户额度；通常按实际 Token 用量计费。开发和自动化测试完全使用模拟响应，不需要 API Key，也不会访问模型服务。

## 测试

```bash
uv run pytest
```

测试会模拟 SSE/SDK 流事件，覆盖异步流程、配置合并、取消与 TUI，不读取真实密钥。
