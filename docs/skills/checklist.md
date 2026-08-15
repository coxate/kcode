# Kcode Skill MVP + Fork Checklist

> `KCODE_SKILL_TRUST_PATH` 仅用于隔离测试；正式默认仍为 `~/.kcode/skill-trust.json`。验收禁止修改 `HOME` 或真实用户 Skill 信任文件。

## 功能验收

- [x] **AC1 解析：** 合法最小 Skill 可加载；未知字段、非法名称/枚举、空正文和多行描述仅跳过自身，warning 不含正文。（验证：Skill parser 测试）
- [x] **AC2 预算：** 32 KiB、30 项 Catalog、5 项 Active、64 KiB 正文的边界值可用；超限不改变旧状态。（验证：parser、catalog、runtime 边界测试）
- [x] **AC3 参数：** `$ARGUMENTS` 四种组合和多占位符渲染正确，原参数不被改写。（验证：parser 与命令测试）
- [x] **AC4 覆盖：** 项目覆盖用户、用户覆盖内置，移除高优先级版本并重启后正确降级。（验证：Catalog 测试）
- [x] **AC5 信任：** 首次项目 Skill、内容变化均请求确认；拒绝后仍可使用内置和用户 Skill。（验证：信任存储、Textual 启动测试）
- [x] **AC6 路径安全：** 符号链接、越界、二进制、非法 UTF-8 和缺失文件被跳过，其它 Skill 正常可用。（验证：parser 测试）
- [x] **AC7 最终校验：** MCP 工具注册后再校验；未知工具和命令冲突项不出现在列表、帮助或补全中；最终 Registry 冻结。（验证：MCP/UI/命令测试）
- [x] **AC8 正文刷新：** 用户正文安全刷新，失败回退缓存；项目正文变化后只执行已信任缓存并提示重启。（验证：Catalog 测试）
- [x] **AC9 内置资源：** 安装后的 wheel 可读取 `commit`、`review`、`test`，且工具名均有效。（验证：wheel 成员检查和 packaging 测试）
- [x] **AC10 命令：** 默认 `/help` 显示 16 条；`/skill`、详情、补全、三个动态命令和空状态正确。（验证：commands、command menu、App 测试）
- [x] **AC11 Prompt：** 稳定 Prompt 只有名称和描述；激活前无正文，激活后的下一模型迭代出现 Active SOP。（验证：prompting、agent loop 测试）
- [x] **AC12 LoadSkill：** Plan Mode 和 fork 白名单中始终可见；未知名和超限不污染状态；重复激活保持顺序和数量。（验证：runtime、tool executor 测试）
- [x] **AC13 状态快照：** 每次激活追加最新名称快照；日志失败时内存仍生效并显示持久化降级 warning。（验证：journal、runtime 测试）
- [x] **AC14 Clear/Resume：** clear 后为空；resume 恢复最后快照，跳过失效项；旧日志恢复为空且不致命。（验证：history、resume UI 测试）
- [x] **AC15 Inline：** `/commit`、`/test` 使用主权限、持久化和记忆流程；UI 只显示标签，模型收到完整 Prompt。（验证：executor、App 集成测试）
- [x] **AC16 Fork 历史：** `none` 无主历史；`recent` 仅复制最后两个纯文本完整轮次，不生成孤立工具结果。（验证：fork executor 测试）
- [x] **AC17 Fork 权限：** 白名单外工具不可见且不可执行；嵌套激活不能扩权；Provider、模型、权限模式和迭代上限保持不变。（验证：executor、permissions 测试）
- [x] **AC18 Fork 回流：** 成功和非取消失败形成完整 user/assistant 对；取消不写主历史且 TUI 恢复输入。（验证：executor、App、JSONL 测试）
- [x] **AC19 用量：** fork Token 进入请求/session UI 统计，不改变主上下文锚；回流轮次可恢复并进入记忆候选。（验证：executor、agent、memory 集成测试）
- [x] **AC20 安全回归：** 测试、lint、改动文件格式检查通过；warning、工具结果和日志不泄漏正文或敏感值。（验证：全仓检查和敏感值断言）

## 集成与兼容

- [x] 有 MCP、无 MCP、MCP 失败三条启动路径都在 Skill 收尾后才启用输入。
- [x] Available Skills 保持在稳定 Prompt；Active Skills 只在动态环境更新。
- [x] 受限 Tool Registry 的定义暴露与实际执行范围一致。
- [x] 新 `skill_state` 不改变现有消息格式；旧版本可跳过，新版本可读取旧日志。
- [x] 普通消息、Plan/Do、权限审批、长期记忆和现有 Slash Command 回归行为不变。
- [x] `KCODE_SKILL_TRUST_PATH` 仅覆盖 Skill 信任文件；未设置时仍使用安全默认路径。

## 自动化检查

- [x] 定向测试全部通过：`UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest tests/test_skill_parser.py tests/test_skill_catalog.py tests/test_skill_trust.py tests/test_skill_runtime.py tests/test_skill_executor.py tests/test_skill_packaging.py tests/test_commands.py tests/test_history_codec.py tests/test_history_journal.py tests/test_history_store.py tests/test_history_runtime.py tests/test_agent_loop.py tests/test_app.py tests/test_mcp_ui.py tests/test_resume_ui.py -q`
- [x] 全仓 `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run pytest` 通过，结果高于原 `291 passed, 2 skipped` 基线。
- [x] `UV_CACHE_DIR=/private/tmp/kcode-skill-uv-cache uv run ruff check .` 通过。
- [x] 本次新增和修改 Python 文件的 `ruff format --check` 通过。
- [x] `git diff --check` 无输出，diff 不包含无关用户改动。
- [x] wheel 构建成功且包含三个内置 `SKILL.md`。

## tmux 端到端

- [x] 使用临时项目和临时 `KCODE_SKILL_TRUST_PATH` 启动真实 `uv run kcode`；不修改 `HOME` 或真实 `~/.kcode`。
- [x] 首次项目 Skill 出现信任界面；批准后 `/skill` 可见，修改内容并重启后再次确认。
- [x] `/help` 显示 16 条，`/skill` 显示三个内置及有效覆盖项。
- [x] `/commit 参数` 以简短标签显示，模型和 JSONL 中保存完整渲染 Prompt。
- [x] `/review` 通过 fork 执行，工具边界、Token 累计和主历史回流符合要求。
- [x] 自然语言触发 `load_skill` 后，同一 Agent Loop 下一次迭代出现 Active SOP。
- [x] `/clear` 后 Active 为空；`/resume` 后按最后快照恢复有效 Skill。
- [x] fork 执行和审批等待期间按 Ctrl+C，主历史不新增轮次且输入恢复。
- [x] 正常退出后检查 session JSONL 成对、可恢复、无 Skill 正文快照和敏感信息。

## 验收报告

- **自动化：** `321 passed, 2 skipped`；Ruff lint 与 `git diff --check` 通过。
- **格式：** 36 个本功能自有或原先已格式化文件通过 `ruff format --check`；工作区原有未格式化文件保持未动，符合批准范围。
- **打包：** `/private/tmp/kcode-skill-dist/kcode_ai-0.7.0-py3-none-any.whl` 构建成功，三个内置 `SKILL.md` 均存在。
- **Prompt：** 本地假 Provider 记录显示 LoadSkill 前 `active_skills=false`，同一 Agent Loop 下一请求为 `active_skills=true`。
- **Session：** JSONL 记录 `skill_state: ["review"]`，未保存正文；clear 后新 session 为空，resume 后首个请求恢复 Active Skill。
- **Fork：** `/review concurrency` 成功回流并累计 session Token；`/review cancel-me` 经 Ctrl+C 取消后恢复输入且未写主历史。
- **信任：** 临时项目首次启动请求确认，内容变化重启后再次确认；全程使用临时 `KCODE_SKILL_TRUST_PATH`，未修改真实 `~/.kcode`。
