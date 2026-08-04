# KCode

KCode 是一个 Python 全屏终端 AI 编程助手。它提供流式 Markdown、多轮会话、Claude extended thinking，以及 Anthropic、OpenAI 和 DeepSeek 三种配置方式。

0.2.0 内置六个工具：读取文件、新建文件、唯一匹配修改文件、执行命令、按 glob 查找文件和按正则搜索代码。每轮最多执行一个工具；自动 Agent Loop 留在后续版本。

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

命令：`/help`、`/clear`、`/exit`。模型生成时按 Ctrl+C 只取消当前回答；空闲时按 Ctrl+C 退出。

## 六个工具的界面验收

0.2.0 每轮最多执行一个工具，因此验收时请让模型直接调用指定工具。建议在临时目录启动 KCode，并依次输入：

1. `只调用 write_file，新建 acceptance-note.txt，内容为：KCode write passed`
2. `只调用 read_file，读取 acceptance-note.txt 的第 1 到 20 行，然后概括内容`
3. `只调用 edit_file，把 acceptance-note.txt 中唯一的 KCode write passed 替换为 KCode edit passed`
4. `只调用 run_command，在当前目录执行 pwd，并解释退出码和输出`
5. `只调用 find_files，在当前目录查找 *.txt`
6. `只调用 search_code，在当前目录搜索 KCode edit passed，文件模式为 *.txt`

工具卡片使用黄色边框与标题，表示本地工具动作，而不是模型的第二条回复；模型回复使用蓝色边框。卡片会列出中文参数摘要，并以绿色成功状态或红色失败状态说明实际执行结果。`write_file` 成功时会明确显示目标文件和写入字节数。

## API Key 是否需要购买

KCode 本身不提供模型额度。调用哪个供应商，就需要该供应商可用的 API Key 和账户额度；通常按实际 Token 用量计费。开发和自动化测试完全使用模拟响应，不需要 API Key，也不会访问模型服务。

## 测试

```bash
uv run pytest
```

测试会模拟 SSE/SDK 流事件，覆盖异步流程、配置合并、取消与 TUI，不读取真实密钥。
