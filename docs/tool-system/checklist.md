# KCode 工具系统 Checklist

- [x] 六工具统一注册，Schema 可导出且重复名称失败。
- [x] 分段读取无遗漏；写文件不覆盖；编辑仅唯一匹配成功。
- [x] 命令返回完整状态，超时/取消后无残留子进程。
- [x] 查找与搜索稳定排序、支持 glob/正则、限制和警告。
- [x] 真实路径授权、工作区外写改确认、严格只读命令白名单正确。
- [x] 所有错误结构化、结果受限且 API Key 完成脱敏。
- [x] OpenAI/DeepSeek 工具碎片正确拼接。
- [x] Anthropic 工具碎片及 thinking/redacted thinking 签名正确往返。
- [x] 单工具两请求顺序正确；多调用、未知工具和第二次工具请求不执行。
- [ ] 完整轮次才提交历史，四阶段 Ctrl+C 不污染历史。
- [x] TUI 可见工具参数摘要、授权、执行、耗时和结果。
- [x] 原有纯对话、配置、Markdown、thinking 和本地命令测试通过。
- [x] `uv run python -m compileall src` 退出 0。
- [x] `uv run pytest` 全部通过且默认不联网。
- [ ] tmux 真实 DeepSeek 读取与授权场景通过，捕获输出无密钥。
