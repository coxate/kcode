# KCode

KCode 是一个 Python 全屏终端 AI 编程助手。它提供流式 Markdown、多轮会话、ReAct Agent Loop、Plan Mode、Claude extended thinking，以及 Anthropic、OpenAI 和 DeepSeek 三种配置方式。

0.4.0 内置六个工具和分层权限系统：读取文件、新建文件、唯一匹配修改文件、执行命令、按 glob 查找文件和按正则搜索代码。模型可以在一次请求中连续调用多轮工具；相邻只读工具有界并发，有副作用的工具按顺序串行。

每次请求默认最多运行 10 个模型轮次。模型正常完成、达到迭代上限、用户取消、连续请求未知工具或模型流出错时，界面都会显示明确的停止原因。

## 权限系统

- 所有文件工具都被限制在启动 KCode 的项目根内，软链接不能绕过沙箱。
- 内置危险命令黑名单先于所有规则和模式生效，不能由 bypassPermissions 放开。
- default 模式自动允许只读文件工具，写入和 Bash 需要确认。
- acceptEdits 自动允许项目内文件编辑，Bash 仍需确认。
- plan 只允许文件读取和严格白名单只读命令。
- bypassPermissions 自动允许普通调用，但仍受黑名单、Plan 约束和路径沙箱保护。
- `write_file` 只创建新文件，已有文件必须使用唯一匹配的 `edit_file`。
- 授权框支持允许本次、永久允许和拒绝本次；永久规则写入本地权限文件。
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
    context_window: 64000
```

DeepSeek 使用 OpenAI 兼容协议，因此 `protocol` 保持为 `openai`。Claude 的 `thinking: true` 会开启独立 thinking 区域；该设置对 OpenAI/DeepSeek 会被忽略并显示提示。
`context_window` 是可选的模型上下文窗口覆盖值；未配置时 KCode 会优先使用已知模型元数据，再使用保守默认值并降低预算估算置信度。

权限规则使用独立文件，优先级为本地 > 项目 > 用户：

- 用户级：`~/.kcode/permissions.yaml`
- 项目级：`<当前工作目录>/.kcode/permissions.yaml`（可提交共享）
- 本地级：`<当前工作目录>/.kcode/permissions.local.yaml`（自动忽略）

可从 `permissions.example.yaml` 复制。文件顶层支持 `defaultMode`、`allow` 和 `deny`；规则使用 `Bash(...)`、`Read(...)`、`Write(...)`、`Edit(...)`、`Glob(...)`、`Grep(...)`。文件规则中的 `*` 不跨目录，`**` 可以跨目录。

## 启动

```bash
uv run kcode
```

也可以：

```bash
uv run python -m kcode
```

命令：`/plan`、`/do`、`/help`、`/clear`、`/exit`。Shift+Tab 循环切换四档权限模式。模型生成时按 Ctrl+C 只取消当前任务；空闲时按 Ctrl+C 退出。

## Agent Loop 与 Plan Mode

- 普通启动使用权限配置指定的默认模式；未配置时为 default。
- 输入 `/plan` 后进入只读规划模式。模型只能读取、查找、搜索，以及执行严格白名单内的只读命令。
- 在 Plan Mode 中输入任务，KCode 会自主调查并保存最终计划。
- 输入 `/do` 切回执行模式。保存的计划只注入下一条普通请求一次，随后自动清除。
- Shift+Tab 离开 plan 会保留计划但不会批准；只有 `/do` 会批准计划。
- 输入 `/clear` 会同时清除对话、计划并恢复启动默认模式。

底部状态栏显示当前权限模式、模型、轮次和本次请求累计 Token。供应商没有返回用量或流提前中断时显示 `Token ?`，不会把未知用量误记为零。

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
