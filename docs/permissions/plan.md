# KCode 权限系统 Plan

## 架构概览

新增 `kcode.permissions`，由配置加载器、黑名单、沙箱、规则匹配器和 PermissionEngine 组成。Engine 为同步、无 UI 的核心；ToolExecutor 在参数预检后调用 Engine，Scheduler 只接收准备结果。人在回路和永久持久化位于执行阶段，Provider 不感知权限。

## 核心接口

- `PermissionMode`: default、acceptEdits、plan、bypassPermissions。
- `PermissionVerdict`: allow、deny、ask；内部 `None` 表示未命中继续。
- `ApprovalChoice`: allow_once、allow_always、deny。
- `PermissionLayer`: 层名、路径、默认模式、allow/deny 规则。
- `PermissionDecision`: 裁决、来源、原因、命中规则和永久精确规则。
- `PermissionEngine.evaluate(call, arguments, context, mode)` 计算裁决。
- `PermissionConfigLoader.load(user, project, local)` 返回设置和警告。
- `LocalPermissionStore.append_allow(rule)` 原子更新并返回新 local layer。

## 模块与交互

- 工具固定映射为 Bash/Read/Write/Edit/Glob/Grep；文件规则比对解析后的 POSIX 相对路径，Bash 比对 trim 后完整命令。
- 黑名单覆盖根/家目录强删、设备写入、格式化、fork bomb 和 Windows 盘根强删，使用危险/近似安全双向语料验证。
- 沙箱解析项目根、既有软链接和不存在尾部，使用路径相对关系判断。
- 文件 glob 的 `*` 不跨 `/`、`**` 跨 `/`；Bash 二者等价，其他 glob 元字符按字面量。
- local→project→user，每层 deny→allow；规则 Allow 不能越过黑名单、Plan 约束或沙箱。
- Plan 白名单 Bash 标为只读；其他 Bash 和写调用直接拒绝。普通模式 Bash 始终按 COMMAND 分类。
- 永久写入使用同目录临时文件、0600、flush/fsync 和原子替换；成功后替换 Engine 内存 local layer。
- Session 保存启动模式、当前模式、最新计划和一次性 approved plan；Runner 每次模型迭代读取当前模式。
- ApprovalScreen 返回三态选择；状态栏左侧显示权限模式，Agent 区只显示迭代、Token 和阶段。

## 文件组织

权限代码位于 `src/kcode/permissions/` 的 models、commands、blacklist、sandbox、rules、config、engine。纯执行路径函数移至 `tools/paths.py`；Executor、Session、Runner、CLI 和 TUI 接入。新增权限测试、示例配置、README 文档并升级至 0.4.0。

## 技术决策

- 权限配置与 Provider 主配置分离，损坏时可安全降级。
- Plan 是任务硬约束加权限模式，普通规则不能放宽。
- Bash 获准后一律使用 shell；黑名单明确是启发式而非完整 shell 沙箱。
- 文件启动时加载一次；永久写入即时更新内存，手工编辑不热加载。
