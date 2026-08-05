# KCode 权限系统 Checklist

> `[x]` 项均有离线自动测试或只读命令证据。末尾人工 smoke 用于发布前体验确认，
> 不作为本地提交阻断条件；未执行时必须保持未勾选。

## 配置与规则

- [x] 三层路径、优先级、同层 deny 优先和默认模式正确。
- [x] 单层缺失/损坏安全降级，warning 不泄漏内容。
- [x] 六友好名、精确/`*`/`**` 匹配符合 Spec。
- [x] 永久规则去重、原子写入、失败保护和内存同步正确。

## 硬边界与模式

- [x] 危险命令语料全部拦截，近似安全语料不误报，shell 未执行。
- [x] 五文件工具阻止项目外、相似前缀、`..` 和软链接逃逸。
- [x] 参数预检与黑名单→Plan→沙箱→规则→模式→审批顺序正确短路。
- [x] default、acceptEdits、plan、bypassPermissions 矩阵正确且硬边界不可绕过。

## 交互与 Agent

- [x] Shift+Tab、`/plan`、`/do`、`/clear` 和状态栏符合 Spec。
- [x] 审批三项菜单、方向键、数字键、永久允许和取消恢复正确。
- [x] Bash 经不同授权来源后的 shell 行为一致，超时/取消/脱敏不退化。
- [x] 拒绝回灌及混合批次保持调用 ID、顺序和 Loop 连续性。
- [x] 只读并发、Ask/副作用串行、Conversation 历史合法。
- [x] Anthropic/OpenAI/DeepSeek 的离线 fake stream 行为一致且 Provider 无权限分支。

## 工程质量

- [x] 敏感配置和 local 权限被忽略，项目权限可跟踪。
- [x] 示例与 README 可用，包、项目和 banner 为 0.4.0。
- [x] 测试不执行危险命令、不读取系统敏感文件、不联网。
- [x] `uv run python -c "import kcode; import kcode.permissions"` 通过。
- [x] `uv run pytest`、Ruff format/check、`git diff --check` 全部通过。
- [x] 用户无关改动与未跟踪文件未被修改或纳入。

## 发布前人工 Smoke（非提交阻断）

- [ ] 在真实终端启动 KCode，目视确认四档状态栏、Shift+Tab 循环和窄窗口布局。
- [ ] 对安全的项目内写入分别操作方向键、回车、数字键和 Esc/Ctrl+C，确认交互手感与提示文案。
- [ ] 选择一次“永久允许”，重启 KCode 后确认 local 精确规则仍生效且不会再次询问。
- [ ] 使用临时项目手工验证项目规则与本地规则冲突时，本地层优先且 deny 优先 allow。
- [ ] 如准备发布到真实模型账户，选择一个 Provider 做低成本 smoke，确认拒绝结果回灌后模型能调整策略；不要求为提交产生 API 费用。
