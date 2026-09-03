# Aozora · 执行治理实施计划

> **面向执行型 Agent：** 按任务逐项执行；优先使用独立执行者与任务级审阅。所有步骤使用复选框记录。

**目标：** 收口生产基线并发布按依赖排序的执行路线图，使后续 Agent 能安全进入 Aozora 功能开发。

**架构：** `docs/APP-CONTEXT.md` 保持产品与架构的长期权威；`docs/milestones/` 维护实时状态、执行顺序和交接。风险只根据已验证事实更新，实施参数必须显式交由项目所有者确认。

**技术栈：** Markdown、Git、GitHub。

**设计规格：** `docs/superpowers/specs/2026-09-03-execution-governance-design.md`

## 全局约束

- 每项任务使用 `T-M*-**` 编号，并按 `docs/engineering/需求变更规范.md` 记录范围、依赖、风险、回退与验收。
- 不改变 D-PROD-01～D-PROD-11、Kotlin WebView + React 架构、整数分金额、云端权威或 AI 动作边界。
- 不提交密钥、签名材料、环境值、APK、AAB 或构建产物。
- `AGENTS.md` 和 `docs/engineering/需求变更规范.md` 是用户已授权的原始文档，只暂存，不作内容改写。

---

### 任务 0：已生成治理文档中文化

**文件：** 修改 `docs/superpowers/specs/2026-09-03-execution-governance-design.md`、`docs/superpowers/plans/2026-09-03-execution-governance.md`。
**交付：** 完整中文文档，保留路径、命令、任务编号、提交哈希和 D-PROD/D-EXEC 标识。
**验证：** `rg -n 'Goal:|Architecture:|Tech Stack:|Files:|Interfaces:|Expected:|Step [0-9]|Task [0-9]'` 无匹配，`git diff --check` 通过。

### 任务 1：收口 T-M0-09 与构建风险

**文件：** 修改 `docs/milestones/T-M0-09-production-rebaseline.md`、`docs/risk-register.md`。
**交付：** 记录 `71e7b2c`、`db1f1cf`、基线/Web/Android 验证和私有远端；R-PROD-06 标记为已缓解。
**验证：** `rg -n '71e7b2c|db1f1cf|R-PROD-06|已缓解'` 与 `git remote -v` 一致。

### 任务 2：修复任务图与入口闸门

**文件：** 修改 `docs/APP-CONTEXT.md`、`docs/milestones/overview.md`。
**交付：** 新增 T-M0-10、T-M1-05；M1-04 依赖 T-M1-05；声明 G0 与下一个候选任务。
**验证：** `rg -n 'T-M1-05|T-M0-10|功能开发前' docs/APP-CONTEXT.md docs/milestones/overview.md`。

### 任务 3：发布长期执行规划

**文件：** 创建 `docs/milestones/production-execution-plan.md`。
**交付：** G0–G6 闸门、M0–M6 依赖顺序、D-EXEC-01～06、交接模板。
**验证：** `rg -n 'G0|G5|G6|D-EXEC-0[1-6]|交接' docs/milestones/production-execution-plan.md`。

### 任务 4：验证、提交与发布

**文件：** 暂存任务 0–3 的文档，以及用户授权的 `AGENTS.md`、`docs/engineering/需求变更规范.md`。
**交付：** 无格式错误、无敏感材料的已推送文档基线。
**验证：** `git diff --check`、`powershell -ExecutionPolicy Bypass -File tools/validate-baseline.ps1`、`git status --short --branch`。