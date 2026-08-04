# KCode 纯对话 MVP Checklist

## 实现完整性

- [x] 两种入口启动同一应用；用户/项目配置正确合并，环境密钥不泄露。
- [x] Anthropic、OpenAI、DeepSeek 经统一入口产生流事件。
- [x] 仅完整成功回答进入历史；本地命令不访问 Provider。
- [x] 80×24 界面按规定显示 banner、版本、目录、就绪行、聊天、输入框和状态栏。
- [x] 文本、Markdown、代码和 thinking 在生成期间可观察到增量更新。
- [x] 生成取消、空闲退出及四类 Provider 错误均正确恢复或清理。

## 集成与测试

- [x] 配置驱动当前 Provider/模型状态，项目配置支持同名字段继承。
- [x] 第二轮包含上一轮成功历史，`/clear` 后历史为空。
- [x] `python -m compileall src` 及 `uv run pytest` 全部通过。
- [x] 自动化测试不连接网络、不读取真实 API Key；示例 YAML 无密钥。

## 端到端场景

- [x] Anthropic 模拟流：thinking 实时展开，答案流式渲染，完成后 thinking 折叠，第二轮携带历史。
- [x] DeepSeek 配置：状态栏正确，兼容流可显示，thinking 给出忽略提示。
- [x] 收到部分内容后 Ctrl+C：流关闭、不写历史，下一次提问成功。
- [x] 环境变量缺失：TUI 不启动，错误包含文件、字段与修复建议且无秘密。
