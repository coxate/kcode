# Kcode Hook 生命周期挂钩系统 Tasks

> 审批状态：已批准。共32项，按依赖执行，每项完成后运行定向验证。

1. 落盘四份批准文档；扫描旧 MewCode、`.mewcode`、11事件和 SubAgent 残留。
2. 抽取 `kcode.matching` glob helper，运行权限回归。
3. 新建 Hook 事件、Action、Context、Catalog、结果与错误模型。
4. 实现条件 tokenizer、四操作符、AND/OR 与 matcher 编译。
5. 实现上下文字段、稳定 JSON、普通/shell-safe 模板、`$$` 与脱敏。
6. 实现配置文件类型、边界、大小、UTF-8、二进制和 YAML 安全读取。
7. 实现项目指纹、独立信任存储、原子写入与 `KCODE_HOOK_TRUST_PATH`。
8. 实现用户+项目 Catalog、ID冲突、严格约束、100条上限和事件索引。
9. 扩展 SessionRuntime 与 Hook reminder，确认 History Codec 不持久化。
10. 实现 HookRuntime 的 once、64 KiB prompt、8并发、warning 与 close。
11. 实现 command action 的 cwd、输出、timeout、取消与进程组清理。
12. 实现 prompt action 的 reminder、转义、顺序、预算和一次消费。
13. 声明 httpx 并实现 HTTP action 的模板、默认 JSON、timeout 和有限响应。
14. 实现普通 HookEngine：条件、once、Plan、同步/异步和错误隔离。
15. 实现 pre-tool reject-only、reject+action、reason 与首拒绝停止。
16. 定义 `ValidatedToolCall` 并从 prepare 抽取 validate。
17. 抽取 authorize，保留 prepare 包装，实现 hook_rejected。
18. AgentRunner 接入 turn、pre_send/post_receive、Hook reminders 和终态。
19. AgentRunner 接入 pre/post tool、permission_request 与 file_change。
20. 接入自动/紧急/手动 compact 与非递归 error。
21. 新建 HookTrustScreen，显示摘要和风险，不显示 action 正文。
22. App 接入 Hook信任、MCP后Catalog、startup/session_start 与输入门控。
23. 接入 clear、resume、exit/on_unmount 的幂等 session/shutdown。
24. 扩展 CommandHost/Dispatcher 的 command_execute 与 error 接缝。
25. 注册 `/hooks`，实现安全列表、空状态、帮助17条与补全。
26. 实现 Textual 后台 warning 监控和无UI stderr 兜底。
27. Skill fork 继承 Engine 与父 session 状态，不绕过 reject。
28. CLI 完成 builder/store/runtime/executor/engine 注入和关闭。
29. 补齐预算、敏感值、Plan/bypass、未信任零执行安全回归。
30. 运行全部 Hook、Agent、Tool、Command、App、MCP、Resume、Skill 定向测试。
31. 运行全仓 pytest、ruff、format、diff、wheel 和安装冒烟。
32. tmux 使用临时项目、信任路径、本地 HTTP、假 Provider 验证全部端到端场景，并将证据写回 Checklist。

执行主链：`模型/解析 → Catalog/Runtime/Executor → Engine → Tool/Agent → App/Command/Fork → 安全回归 → 全仓 → E2E`。
