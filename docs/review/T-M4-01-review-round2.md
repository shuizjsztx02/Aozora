# T-M4-01 审查报告（第 2 轮）：修复核验与收口

- **审查日期：** 2026-09-06
- **审查模式：** Review + Fix（第 1 轮为只读审查；本轮经用户指示进入修复：按第 1 轮结论修复遗留问题、精确提交并送 CI）
- **基线：** HEAD `7228493`（含并行会话的 `f716cdd`、`7228493` 两笔 CI 修复提交）
- **范围：** 第 1 轮报告（`docs/review/T-M4-01-review.md`）R-M4-01 ~ R-M4-07 的修复核验 + T-M4-01 职责内的剩余修复
- **边界说明：** 工作树内存在并行会话的 T-M4-03/04/05/06 未提交改动（prompts.py、fact_pack.py、test_narrative_report.py、test_stats_exports.py、app/ 客户端文件、两份权威文档的其他区段）。本轮**未提交、未修改**任何归属其他任务的内容；对两份脏文档（ai-architecture.md、production-execution-plan.md）仅提交本轮契约澄清 hunk（部分暂存），其余改动原样留在工作树。

---

## 一、第 1 轮问题逐项核验

| ID | 级别 | 第 1 轮结论 | 本轮核验 | 修复位置 |
|---|---|---|---|---|
| R-M4-01 | P1 | 实现全量未提交 | ✅ **已由后续提交解决**：`server/src/modules/ai/` 全目录、AI 测试、`docs/adr/`、config/main/models 已随 `c101afb`（T-M4-02）、`435342b` 入库；`git ls-files` 确认无 untracked 残留 | `c101afb`、`435342b` |
| R-M4-02 | P2 | closure 报告测试数字失真 | ✅ **本轮修复**：测试结果节以实测订正（142 项逐文件计数 + 完整套件 364/1/1 及 error 归因）；"test_migrations 2 失败需独立 PR"改注已解决（实测 12 passed）；后续建议 #3 同步订正；基线行补 R-M4-07 未落盘注记 | `T-M4-01-closure.md` |
| R-M4-03 | P2 | interpret 契约与权威文档脱节 | ✅ **本轮修复（取第 1 轮建议方案 ①，改文档对齐实现）**：ai-architecture.md 新增「interpret → validate → execute 流水线」节，明确 interpret 输出为最小解析结果（非提案）、写库必经 /ai/validate + /ai/execute；执行计划 01d 行「文本 → ActionProposal」改为精确流水线描述 | `docs/architecture/ai-architecture.md`、`docs/milestones/production-execution-plan.md` |
| R-M4-04 | P2 | ruff 依赖未声明，server CI job 必红 | ✅ **已修复（并行会话 `7228493`）**：`pyproject.toml` dev 依赖新增 `ruff>=0.8,<1`。本轮在纯净 HEAD worktree 实测 `ruff check src/ tests/` **All checks passed**（即依赖补齐后 lint 本身无违规）；远端 CI run `34037172838` 的 server ruff 步骤为最终确证 | `7228493` |
| R-M4-05 | P3 | validator.py docstring 引用不存在的 assembly.py | ✅ **本轮修复**：docstring 改为「组装在客户端（ADR-003）；DB 上下文经 context.py 在 /ai/validate 时应用」，与实现一致 | `server/src/modules/ai/validator.py` |
| R-M4-06 | P3 | JSON 重试路径首次成功调用的 tokens 漏计 | ✅ **本轮修复**：interpret 循环在 ValueError 重试分支累计 `retry_prompt_tokens/retry_completion_tokens`；`_record_usage` 新增 keyword-only `extra_*_tokens` 参数（report 路径调用点不受影响）；AIUsage 行与响应 `usage` 均含全部 attempt 的 tokens。新增回归测试 `test_retry_accumulates_tokens_from_invalid_json_attempt`（断言审计行 tokens == 90+30+120+45、响应 usage 分项累计） | `server/src/modules/ai/service.py`、`server/tests/test_ai_gateway.py` |
| R-M4-07 | P3 | 上一轮评审报告未落盘 | ✅ **已解决**：第 1 轮报告已提交（`3e511ed`）；本轮沿用 R-M4-nn 编号并落盘本文档 | `docs/review/` |

**观察项 O-1 ~ O-4：** 维持挂账，无变化（均为可辩护设计取舍，第 1 轮已详述）。

---

## 二、本轮验证命令与实测结果

| 命令 | 结果 | 备注 |
|---|---|---|
| 纯净 HEAD worktree（`435342b`）`ruff check src/ tests/` | ✅ All checks passed | 证明 R-M4-04 只缺依赖声明，代码本身 lint 干净 |
| 纯净 HEAD worktree `pytest tests/ -q` | ⚠️ 394 passed, **1 failed, 3 errors** | 失败均非 01 模块，见三、N-1/N-2 |
| 主树模块 01 套件（config/auth/main/ai_gateway/parity/validation，含本轮修复） | ✅ **142 passed** | 逐文件 14/40/6/49/17/16 |
| `ruff check` 本轮改动文件 | ✅ All checks passed | service.py / validator.py / test_ai_gateway.py |
| `gh run list` | run `34037172838`（`7228493`）in progress | client/android/server 三 job 最终状态以该 run 为准 |

---

## 三、本轮新登记发现（均非 01 模块，记录归属）

### N-1（归属 T-M4-03）HEAD 既有破损：server job pytest 步骤在纯净克隆下仍会失败

- **位置：** HEAD 已提交的 `server/tests/test_narrative_report.py::TestNarrativeCache`（2 项 error：`fixture 'db_session' not found`——HEAD 版测试未 import `tests.fixtures_db` 的共享 fixture）；HEAD 已提交的 `server/tests/test_stats_exports.py::test_monthly_stats_balance_non_negative`（1 项 failed：`assert -10000 == 0`——已提交的 statistics 代码返回负结余，已提交的测试仍断言 clamp 到 0）。
- **影响：** 即使 ruff 依赖补齐（R-M4-04），远端 server job 的 `pytest tests/` 步骤仍红。这是 R-M4-17（T-M4-03 审查）"部分提交导致 HEAD 不自洽"的残留形态。
- **归属与现状：** 修复已存在于工作树的 T-M4-03 未提交改动中（`test_narrative_report.py` +156 行含 `db_session` import；`test_stats_exports.py` 改名为 `test_monthly_stats_balance_can_be_negative` 并断言 -10000）。属并行 T-M4-03 会话的待提交原子改动，本轮**不代为提交**（避免拆散其任务内原子性，重蹈 R-M4-01/17 覆辙）。
- **关闭条件：** T-M4-03 会话提交其测试修复后，纯净 HEAD `pytest tests/` 全绿、远端 server job pytest 步骤绿。

### N-2（归属 T-M4-05，仅记录）Android lint 失败以 @SuppressLint 关闭

- `7228493` 对 `ReminderReceiver.kt:57` 的 `MissingPermission` lint error 添加 `@SuppressLint`，理由为 `hasNotificationPermission` 已在调用前检查（lint 误报）。与 T-M4-05 复审 R-M4-27（POST_NOTIFICATIONS 只查不请）同域；suppress 是否掩盖真实缺陷由 T-M4-05 线自行核验，非 01 范围。

### N-3（环境）Windows 本地 `test_startup_cleanup_removes_stale_files` PermissionError

- 与第 1 轮一致：`C:\Users\admin\AppData\Local\Temp\pytest-of-admin` 权限错误，exports 模块（T-M2-05）、ubuntu CI 不复现，不计入门禁。

---

## 四、结论

**T-M4-01：第 1 轮全部 7 项问题（P1×1、P2×3、P3×3）已关闭**——2 项由后续提交/并行会话解决（R-M4-01、R-M4-04，另 R-M4-07 随审查落盘解决），4 项本轮修复（R-M4-02/03/05/06），修复均有实测支撑（142 passed、ruff 干净）。观察项 O-1~O-4 维持挂账。

**门禁：** 本地模块 01 pytest ✅ 142/142；本地 ruff ✅（纯净 HEAD 全树 + 本轮改动文件）；远端 CI：server job 的 ruff 步骤预期转绿（run 34037172838 起），**pytest 步骤受 N-1（T-M4-03 HEAD 破损）阻塞仍红**——非 01 模块缺陷，关闭条件与归属已在 N-1 记录。

**本轮提交内容（Task: T-M4-01 第 2 轮收口）：** `server/src/modules/ai/service.py`、`server/src/modules/ai/validator.py`、`server/tests/test_ai_gateway.py`、`T-M4-01-closure.md`、`docs/architecture/ai-architecture.md`（仅本轮澄清 hunk）、`docs/milestones/production-execution-plan.md`（仅 01d 行 hunk）、本文档。

---

## 五、补记（2026-09-06 晚 · 提交归属与门禁终态）

1. **提交归属：** 本轮修复完成并精确暂存后、独立提交前，并行会话以 `3e1607b`（T-M4-05 主任务的批量混合提交）将工作树全量入库，本轮 7 个文件的修复被并入该提交并已随其推送远端。共享分支上不做历史重写，接受现状并在此记录归属：`3e1607b` 中属于 T-M4-01 第 2 轮的内容为 service.py（R-M4-06）、validator.py（R-M4-05）、test_ai_gateway.py（+1 回归测试）、T-M4-01-closure.md（R-M4-02）、ai-architecture.md（「interpret → validate → execute 流水线」节，R-M4-03）、production-execution-plan.md（01d 行，R-M4-03）、本文档。上文"本轮提交内容"原计划的独立提交因该批量提交而不再存在。
2. **N-1 已关闭：** `3e1607b` 同时并入了 T-M4-03 的测试修复（`db_session` import、balance 负值断言）与 starlette DeprecationWarning 过滤（pyproject `filterwarnings`）。纯净 HEAD 实测 `pytest tests/` = **404 passed, 1 skipped, 1 error**（error 为 N-3 Windows 环境项，ubuntu CI 不复现）。
3. **N-4（本轮新增，已关闭）：** `3e1607b` 引入 `tests/test_narrative_report.py` 一处 ruff I001（import 块未排序），远端 run `34038958115` server job ruff 步骤因此失败（同 run 的 client、android job 均已转绿）。并行会话 `a5d8507` 已修复（import 块后补空行，零语义改动）；纯净 `a5d8507` 实测 `ruff check src/ tests/` All checks passed、`test_narrative_report.py + test_ai_gateway.py` 68 passed。
4. **门禁终态：** 本地纯净 HEAD（`a5d8507`）ruff ✅ + pytest ✅（404/1/1，仅环境 error）；模块 01 六文件 142/142 ✅。远端 CI 以 `a5d8507` 推送后的 run 为准——该 run 同时将首次实测 pytest 步骤（此前各 run 均死在 ruff/lint 步骤），验证 starlette 过滤是否彻底解决 CI 依赖漂移下的 exit 4（另一并行只读审查登记的 R-M4-38）。
5. **命名注记：** 另一只读并行会话产出未跟踪文档 `docs/review/T-M4-01-review-2.md`（自称"第 3 轮"，基线 `3b73dd5`），与本文档（round2，已随 `3e1607b` 入库）存在轮次编号交叉；两份文档的发现归属以各自文内基线与时间戳为准。
6. **远端 CI 终态（已确证，2026-09-06 晚）：** run `34040351268`（提交 `a6f5c88`，包含本轮全部内容）**三 job 全绿**——Client ✅ 37s、Android ✅ 3m27s、Server ✅ 47s（ruff 与 pytest 步骤均通过，纯净依赖环境下全套测试通过）。此前 pytest 步骤的两层 CI 阻塞（starlette `StarletteDeprecationWarning` 继承 UserWarning 致过滤器失效 exit 4；批量提交引入表单路由但未声明 `python-multipart` 致 232 errors）均由并行会话以 `0d275c3` → `5893bc6` → `a6f5c88` 三连修复，属 T-M4-04/05 交付层问题，非 01 模块缺陷。**T-M4-01 完成门禁（改动已提交推送 + 远端 CI 通过）至此达成。**
