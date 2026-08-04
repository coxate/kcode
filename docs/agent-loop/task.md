# KCode Agent Loop Tasks

## 文件清单

- 新建：本目录四份文档、`src/kcode/session.py`、`src/kcode/tools/scheduler.py` 及 Agent/调度/Session 测试。
- 修改：配置、事件、对话、协调器、Provider、工具执行链、TUI、CLI、README、示例配置和版本。

## 有序任务

1. 写入四份批准文档。验证：无占位符且需求、计划、任务、验收互相覆盖。
2. 增加 AgentConfig 及兼容配置合并。验证：旧配置、覆盖和边界测试。
3. 实现 AgentSession。验证：模式转换、一次性计划、clear 测试。
4. 扩展事件、Token 与流式累积。验证：交错流、多调用、缺失 usage 测试。
5. 实现 Conversation 检查点。验证：完成、限额、取消、流错历史测试。
6. 增加工具效果分类和预处理。验证：六工具分类与 Plan 拒绝测试。
7. 实现安全调度器。验证：只读重叠、并发上限、副作用屏障和稳定顺序。
8. 扩展 OpenAI/DeepSeek Provider。验证：多工具、usage、reasoning 连续状态测试。
9. 扩展 Anthropic Provider。验证：多工具、usage、thinking 与签名测试。
10. 实现 Agent Loop 主路径。验证：三轮工具后正常完成。
11. 实现全部停止状态。验证：每种原因唯一且无多余请求。
12. 实现 Plan Mode。验证：只读边界、计划保存和一次性注入。
13. 接入 TUI。验证：模式、进度、Token、多卡片与四阶段取消。
14. 更新 CLI、README、示例和 0.3.0 版本。验证：两种入口可启动。
15. 运行回归、编译与完整测试并修复失败。
16. 用 tmux 完成离线真实 TUI 验收。
17. 用 tmux 完成真实 DeepSeek 多步只读和经确认写改验收并扫描密钥。
18. 按逻辑分组提交，最终推送 `coxate/kcode` 的 `main`。

## 执行顺序

`文档 → 配置/状态/事件/历史 → 工具执行链 → Provider → Agent Loop → Plan/TUI → 回归 → tmux → GitHub`
