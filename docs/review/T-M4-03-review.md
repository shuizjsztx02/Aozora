# T-M4-03 审查报告：AI 查询与洞察

- **审查日期：** 2026-09-06（第 4 轮复审）
- **范围：** T-M4-03a（事实包服务）+ T-M4-03b（叙述生成与数字校验）。T-M4-03c 仍未开始，不在范围。重点核验第 3 轮登记的 R-M4-08 ~ R-M4-15、R-M4-17 修复状态；修复分散于提交 `c101afb`、`435342b` 与工作树未提交改动三处。
- **基线：** HEAD `435342b` + 工作树未提交改动（`fact_pack.py` 哈希扩展、`prompts.py`、`test_fact_pack.py`、`test_narrative_report.py`、`test_stats_exports.py`、`ai-architecture.md`、交付文档）。远端 main 已领先本地（`fix(T-M4-06)` 提交，网络原因未能拉取，不影响本轮结论——其 CI Server job 同样 failure）。**注：** 审查期间另一会话在工作树并发修改了 `ai/service.py`（interpret 路径 R-M4-06 重试 tokens 修复）、`validator.py`、`test_ai_gateway.py` 并推进了本地 HEAD（`f716cdd`/`7228493`/`3b73dd5`，均为 CI/客户端修复，不触及本轮核验的 /ai/report 链路）；本文 `service.py` 行号以撰写时工作树为准，`_generate_narrative` 的核验结论不受并发修改影响（该修复未覆盖 /ai/report 路径）。
- **模式：** 只读 Review（未修改任何代码/测试/配置；仅更新本审查文档与任务状态文档）。
- **编号说明：** 沿用 M4 连续编号。R-M4-16 ~ R-M4-37 已被其他审查文档（T-M4-05/T-M4-06 等）使用，本轮新增问题编号为 **R-M4-38**。

---

## 一、实测结果（第 4 轮实际运行）

| 检查项 | 命令 | 结果 |
|---|---|---|
| 任务级测试 | `pytest tests/test_fact_pack.py tests/test_narrative_report.py -q` | **36 passed**（上轮 30 + 4 parity + 1 usage + 1 rate limit，与修复方声称一致） |
| 全量服务端测试（工作树） | `pytest tests/ --tb=line -q` | **403 passed, 1 skipped, 1 error**；error 仍为 `test_stats_exports.py` Windows 临时目录 `PermissionError`（已知环境问题）；上轮 4 个 test_attachments 失败已消失（相关工作树改动已随提交入库） |
| Lint（CI 门禁） | `ruff check src/ tests/` | **1 error**（I001 import 排序，`test_narrative_report.py:10`，未提交的工作树版本）。ruff 0.16.6 本机已可用、`pyproject.toml` 已声明 `[tool.ruff]`、CI server job（ruff+pytest）已提交并实际运行——R-M4-04 的工具缺口已消除；但当前工作树提交后 lint 门禁将红 |
| HEAD 收集自洽性 | `git worktree add <tmp> HEAD` + `pytest tests/ --collect-only -q` | **399 collected, 0 errors** ✅——R-M4-17 的"收集失败/无法启动"已修复。裸 `import src.main` 报 `DATABASE_URL` 缺失属预期环境配置（`.env` 为 gitignore，`server/README.md:8` 明文要求配置），非 HEAD 缺陷 |
| HEAD 全量测试（纯净 worktree） | 同上 + `pytest tests/ -q --tb=no` | ❌ **1 failed, 394 passed, 1 skipped, 3 errors**——`test_stats_exports.py::test_monthly_stats_balance_non_negative` FAILED（HEAD 代码返回 -10000，HEAD 测试仍断言 0：测试更新未随 `435342b` 提交）；`TestNarrativeCache` 2 ERROR（`0bbd113` 提交的测试所用 `db_session` fixture import 仅存在于工作树）；另 1 error 为已知 Windows 环境问题。详见核验表 R-M4-17 行 |
| 远端 CI | `gh run view 34035608951`（`435342b`）/ `34036580072`（远端最新） | **Server Lint & Test: failure** ×2——与纯净 HEAD 实测一致（失败为上述红测试，平台无关）。`435342b` 的 Client job success、Android failure 为既存问题；远端最新提交的 Client 亦转红（归属 T-M4-06，非本轮范围） |

## 二、上一轮（第 3 轮 R-M4-08 ~ R-M4-17）问题修复核验

| 原问题 | 第 4 轮核验结果 |
|---|---|
| **R-M4-08**（P1）balance 口径与权威数据模型冲突 | ✅ **服务端修复属实**——采用方案 (b) 且文档先行：`data-model.md:8` 已修订为 `balance = totalIncome − totalExpense`（允许负值，`MoneyCents` 存储层仍非负，赤字由计算结果表达）；`statistics/service.py:156,226` 改 `income - expense`、`statistics/schemas.py:27,38` balance 移除 `ge=0`（income/expense 保留 `ge=0`，正确）；`test_stats_exports.py` 赤字月断言 -10000；事实包顶层与 trend 均不 clamp；新增赤字 parity 测试锁死口径。⚠️ 但修复止步于服务端边界：客户端 `aggregator.ts` 仍 clamp 0 → 新立 **R-M4-38**；且 `test_stats_exports.py` 更新未提交 → HEAD 红（归 R-M4-17 残留） |
| **R-M4-09**（P1）同数据不重复调 LLM | ✅ 保持关闭（第 3 轮已核验，本轮 36 用例中缓存 2 用例仍绿）。但其提交（`0bbd113`）的测试所依赖的 `from tests.fixtures_db import db_session` 仅存在于未提交工作树，纯净 HEAD 上两测试 ERROR——"提交可独立复验"仍未达成，归入 R-M4-17 残留 |
| **R-M4-10**（P2）校验失败/降级路径漏记 tokens | ⚠️ **部分修复**——主路径已修：`_record_usage` 提前至 provider 返回并解析成功后立即执行（`service.py:876-878`，随 `435342b` 提交），数字校验失败仅丢弃叙述、保留计费（`service.py:897-898`），新增 `TestUsageOnVerificationFailure` 断言 AIUsage 行 ✅。**残留**：`invalid_json` 重试耗尽 return（`service.py:867-871`）仍漏记——此时 `result` 非空、tokens 已实际消耗，违反原修复建议"只要 provider 实际返回过 result 就必须记录"；该行注释 "No parseable result → no usage to record" 与事实不符。另重试路径首次 attempt 消耗的 tokens 依旧不记（R-M4-06 同族；工作树内另一会话的在途修复仅覆盖 interpret 路径，未覆盖 `_generate_narrative`） |
| **R-M4-11**（P2）对账不变量无自动化守卫 | ✅ **修复属实**——`TestStatisticsParity` 4 个测试为真实 HTTP 级对账：月度总计（`/ai/report` vs `/stats/monthly` 四项逐项相等）、赤字口径（两侧均 -400000）、趋势（vs `/stats/trends` 6 个月逐月三项对比）、分类求和恒等式。任一侧口径改动即红，满足"人为改动任一侧口径应使测试变红"的复验标准。**未提交**（工作树改动） |
| **R-M4-12**（P3）事实包缓存 TTL 内返回陈旧非哈希字段 | ✅ **修复属实（未提交）**——`_compute_data_hash` 已纳入 `tx_count`、费用/收入分类名、最大单笔的 `id/note/biz_date`（`fact_pack.py:141-176`）；最大单笔的 `category_name` 经分类列表传递覆盖。**微小残留**：`trend.transaction_count`（历史月份）未入 hash——TTL 内历史月份发生金额不变的结构变化（如一笔拆二）时，缓存旧 FactPack 的 trend count 会被返回；影响极小 |
| **R-M4-13**（P3）交付文档失真 | ⚠️ **部分修复**——① 缓存条目 1000→500 ✅；② 297→"393+（第 3 轮实测，伴随 4 个 test_attachments 失败）" ✅ 如实标注，但本轮实测已 403 且 attachments 失败已消失，数字再度滞后，且"全部通过…伴随 4 个失败"措辞自相矛盾；③ `T-M4-03b:58` 改为"口径已统一（statistics 同步放开、data-model.md 已修订）" ✅ 现已属实；⑤ `test_narrative_report.py` 文件头已改为与实际范围一致 ✅（新增两类测试未列入，微瑕）。**新滞后**：`final-summary.md:210,374` 仍写"28 个单元测试"（现为 36）。文档数字反复滞后的根因是"先写文档后跑测试"，建议提交前以可复跑命令刷新 |
| **R-M4-14**（P3）highlights 被丢弃；"本月"措辞 | ✅ **基本修复**——REPORT 模板已移除 highlights 输出要求（`prompts.py:61`）、系统提示改"指定月份"、`user_input` 改"我{period}月度"（`service.py:841`，随 `435342b` 提交）、balance 说明加"可能为负表示赤字" ✅。**残留**：`prompts.py:69-72` 事实键名描述仍为四处"本月总收入/本月总支出/本月结余/本月交易笔数"，历史期间提示词仍含"本月"字样，轻微 |
| **R-M4-15**（P3）限流命中整个 /ai/report 返回 429 | ✅ **修复属实**——代码第 3 轮已核验（429→INFO 日志+return，非 429 仍抛出，`service.py:820-834`）；本轮契约文档与测试补齐：`ai-architecture.md` 新增"/ai/report 限流降级契约"节（200+纯事实包、理由、interpret 保留 429 的边界均明确）✅；新增 `TestRateLimitDegradation` 断言事实包返回、narrative=null、provider 零调用 ✅。契约文档与测试均**未提交** |
| **R-M4-17**（P1）HEAD 不自洽 | ⚠️ **部分修复**——`c101afb` 已将 `server/src/modules/ai/` 全目录 10 文件与 `test_fact_pack.py` 等入库，纯净 HEAD **收集零 error（399 collected）** ✅。**但 HEAD 测试不绿**：纯净 HEAD 全量 **1 failed + 2 errors**（第一节）——根因仍是非原子提交：`435342b` 提交了 statistics 负余额代码却未提交配套测试更新；`0bbd113` 的缓存测试依赖未提交的 fixture import。远端 CI Server job 连续 2 次 failure 实证。另 **`435342b` 标称 docs 提交失实**：夹带 R-M4-08/R-M4-10 代码修复（statistics schemas/service、ai/service.py 19 行行为改动），提交信息仅称"修复 TYPE_CHECKING 导入"，其声称的 CI 验证（pytest 93 passed）只覆盖 T-M4-02 测试 |

## 三、本轮新登记问题

### 🔴 R-M4-38（P1 · 契约一致性/跨端）— 权威口径已修订为允许负余额，客户端仍 clamp 0，对账不变量跨端破坏

- **ID：** R-M4-38
- **级别：** P1
- **类别：** 契约一致性 / 跨端口径
- **状态：** 未修复
- **文件与位置：** `app/src/domain/aggregator.ts:44-55`（`computeBalance` 在 income < expense 时返回 `Money.ZERO`，docstring 声称口径为 "balance = max(0, income - expense)" 并引用 data-model.md）；`app/src/domain/types.ts:86`（`balance: MoneyCents // max(0, income - expense)`）；对照 `docs/architecture/data-model.md:8`（已修订：允许负值，"客户端统计、服务端统计与导出必须使用同一口径"）
- **问题与触发条件：** R-M4-08 修复采用方案 (b)，修订权威文档并统一服务端，但客户端未同步。客户端 `computeBalance` 与 types 注释现在**引用同一份文档却描述相反口径**。赤字月：客户端统计 balance=0，`/stats/monthly` 与 `/ai/report` balance=负值 → APP-CONTEXT §5.2 对账不变量（客户端明细合计 = 客户端统计 = 服务端统计 = 导出）跨端破坏；T-M4-03c 实现后洞察卡与本地统计同屏时用户直接可见矛盾。
- **证据或复现命令：** 代码对照即可确认（上行文件:行号）；或在客户端测试中构造赤字月，对比 `computeBalance` 结果与服务端 `/stats/monthly` 响应（-400000 vs 0）。
- **影响：** R-M4-08 的口径统一目前只覆盖服务端一半；客户端离线统计与服务端在赤字月数字不一致，且客户端注释引用的权威文档已与其实现相反——后续维护者会被误导。
- **修复建议：** 客户端 `computeBalance` 移除 clamp；类型层 `MoneyCents`（非负存储类型）不宜继续承载 balance，需为 balance 引入有符号 cents 表达（如 `SignedCents` 品牌类型或普通整数+注释），UI 同步适配赤字展示（负号/超支措辞）；更新客户端统计测试并增加跨端口径用例（客户端聚合 == 服务端响应 fixture）。归属客户端任务线（T-M2-05 客户端统计/T-M4-00 前端），可独立立单，但应在 T-M4-03c 实现前完成。
- **复验方法：** 赤字月（expense>income）下客户端聚合 balance == 服务端 `/stats/monthly.balance`（同一负值）；客户端 vitest 通过。

### 关联既有问题（不重复建项）

- **R-M4-01（P1 · 流程）：** 持续——本轮修复（R-M4-11/12/14/15 的代码、测试、契约文档与 R-M4-13 文档更正）**全部未提交**；R-M4-17 残留（HEAD 1 failed + 2 errors）与 `435342b` docs 提交夹带代码且信息失实，均为同源后果。
- **R-M4-04（P2 · CI）：** 工具缺口已消除（ruff 本机可用、pyproject 已声明、CI server job 已提交且远端实际运行）；剩余动作：工作树 I001 需在提交前修复，否则 lint 门禁红。
- **R-M4-06（P3 · 审计）：** 与 R-M4-10 残留同族。审查期间工作树出现在途修复（interpret 路径重试 tokens 累计入 `_record_usage`，未提交、不属本轮核验范围）；`_generate_narrative` 的同族残留（重试首次 attempt tokens、invalid_json 耗尽路径漏记）仍未覆盖。

## 四、门禁结论（第 4 轮）

**实现层（服务端 T-M4-03a/03b）：通过（首次）。** 第 3 轮 8 项问题修复全部属实落地（R-M4-10/13/17 部分修复，残留均为 P3 级小项）：口径统一且文档先行、对账不变量有 HTTP 级自动守卫、校验失败计费漏洞已堵、限流降级契约文档化并有测试、哈希覆盖非数值字段。任务级 36 passed、工作树全量 403 passed 全绿（1 个已知 Windows 环境 error）。

**跨端契约：不通过。** 新登记 R-M4-38（P1）：客户端 balance 口径与已修订的权威文档分叉，对账不变量跨端破坏，R-M4-08 的最终关闭受阻。

**交付层：不通过。** HEAD 测试不绿（R-M4-17 残留：纯净 HEAD 1 failed + 2 errors，远端 CI Server job 连续 failure 实证）；本轮修复全部未提交（R-M4-01 持续）；`435342b` docs 提交夹带代码且信息失实；工作树 ruff I001 待修。

**状态处置：** `production-execution-plan.md` 中 T-M4-03 及 03a/03b 状态行已按本轮证据更新（工作区内更新，不随本审查文档提交，避免夹带实现方未提交改动）。

**建议承接顺序：** ① 修 I001 → 单次原子提交全部工作树修复（含 `test_stats_exports.py`、`test_narrative_report.py` fixture import、fact_pack/prompts/测试/契约文档/交付文档）→ 纯净 worktree 全绿 + 远端 CI Server job 转绿（同时关闭 R-M4-17 与 R-M4-01 在本任务范围的形态）→ ② R-M4-38 客户端口径修复（独立客户端任务，T-M4-03c 之前完成）→ ③ P3 残留（invalid_json 漏记、prompts 四处"本月"、trend tx_count 入 hash、交付文档数字刷新）随 T-M4-03c 一并处理，R-M4-06 同族审计问题可随 ③ 统一清理。

---

## 五、第 3 轮审查（历史保留，2026-09-06）

> 以下为第 3 轮审查原文。其核验的问题（R-M4-08 ~ R-M4-17）最新状态以本文件第二节（第 4 轮核验）为准；文中"本轮"均指第 3 轮，"上一轮/第 2 轮"指本文件第六节。

### 5.1 实测结果（第 3 轮实际运行）

| 检查项 | 命令 | 结果 |
|---|---|---|
| 任务级测试 | `pytest tests/test_fact_pack.py tests/test_narrative_report.py -q` | **30 passed**（上轮 28 + R-M4-09 修复新增 2 个叙述缓存测试，与提交 `0bbd113` 声称一致） |
| 全量服务端测试 | `pytest tests/ --tb=line -q` | **393 passed, 4 failed, 1 skipped, 1 error**。4 个 failed 均在 `test_attachments.py::TestAttachmentAPI`（upload 返回 401），初判归属未提交的 attachment 系列改动（T-M4-04，工作树含 `models/attachment.py` 修改与 `0009_attachment_user_id` 迁移），与 ai 模块无 import/调用关系，**非 T-M4-03 引入**（上轮全量 366 passed 无 failure），归因待 T-M4-04 审查确认；error 仍为 `test_stats_exports.py` Windows 临时目录 `PermissionError`（已知环境问题） |
| Lint（CI 门禁） | `ruff check src/ tests/` | 仍无法执行——本机无 ruff 且 `pyproject.toml` 未声明（R-M4-04 持续） |
| HEAD 自洽性 | `git worktree add` 纯净 HEAD + `pytest tests/test_narrative_report.py -q` | **collection error**——HEAD 上已提交的测试 import 不到未提交的 `src.modules.ai.fact_pack`；且 HEAD `main.py:27,54` import 的 `ai.routes`/`ai.provider` 也不在 HEAD（`conftest.py:40` import `src.main` → 全套件连带失败）。详见 **R-M4-17** |

### 5.2 第 2 轮问题修复核验（第 3 轮核验）

| 原问题 | 第 3 轮核验结果 |
|---|---|
| **R-M4-08**（P1）balance 口径与权威数据模型冲突 | ⚠️ **部分修复，未关闭**——`trend` 内 balance 已改为 `income - expense`（`fact_pack.py:497-501`），同一响应内顶层与 trend 的矛盾已消除；但与权威口径的冲突原样存在：`statistics/service.py:156,226` 仍 `max(0, ...)`、`statistics/schemas.py:25-27,36-38` 仍 `Field(ge=0)`、`data-model.md:8` 未修订。赤字月份 `/ai/report.balance`（负值）≠ `/statistics/monthly.balance`（0），对账不变量仍破坏。`T-M4-03b-code-review-improvements.md:58`"与 StatisticsService 口径一致"的声明仍不实，且两侧分歧现已扩大（statistics 连 trend 也 clamp） |
| **R-M4-09**（P1）同数据不重复调 LLM 未实现 | ✅ **修复属实（提交 `0bbd113`）**——叙述缓存（`service.py:55-59,89-117`）键为 `(book_id, period, data_hash)`；命中路径（`service.py:743-758`）不调 provider、不记 AIUsage、`narrative_cached=True` 并记 INFO 日志；未命中路径校验通过后入缓存（`service.py:875-877`）。2 个 mock provider 测试实测通过。缓存判定基于**当次查询**新算出的 `data_hash`，不受 R-M4-12 陈旧 FactPack 对象问题影响。但该提交引发 **R-M4-17**（部分提交导致 HEAD 破损） |
| **R-M4-10**（P2）校验失败/降级路径漏记 tokens | ❌ **未修复**——`service.py:828`（ProviderError 重试耗尽 return）、`service.py:844-860`（数字校验失败 return）仍不经过 `_record_usage`（仅 `service.py:863` 成功路径记录）；guest 配额绕过路径原样存在 |
| **R-M4-11**（P2）对账不变量无自动化守卫 | ❌ **未修复**——`test_fact_pack.py` 仍无任何与 /statistics 的对比断言；唯一相关变化是 `_YearMonth` 更名 `YearMonth` 供 fact_pack import（statistics/service.py 工作树改动，未提交），月度/趋势/分类聚合仍为平行实现 |
| **R-M4-12**（P3）事实包缓存 TTL 内返回陈旧非哈希字段 | ❌ **未修复**——`fact_pack.py:236-240` 缓存检查仍在全部查询之后，命中即返回旧 FactPack 对象 |
| **R-M4-13**（P3）交付文档失真 | ❌ **基本未修复**——`T-M4-03-final-summary.md:224` 仍写"最大缓存条目：1000"（代码 500）；`:375` 仍写"297 个集成测试全部通过"（本轮实测 393 passed 且伴随 4 failed，该声称双重过期）；`test_narrative_report.py:8` 文件头仍声称含"POST /ai/report — 端到端测试"（仍无 HTTP 测试，新增缓存测试为 service 层直调）；`T-M4-03b:58` 不实声明仍在（见 R-M4-08 行）。仅第 6 条（"同数据不重复计费"勾选）随 R-M4-09 修复事后成真 |
| **R-M4-14**（P3）prompt 输出 highlights 被丢弃；"本月"措辞 | ❌ **未修复**——`prompts.py:58,64` 仍要求 highlights 且系统提示称"本月"；`service.py:809` 用户输入仍为"我本月（{period}）"，历史期间叙述时间指代错位依旧 |
| **R-M4-15**（P3）限流命中整个 /ai/report 返回 429 | ⚠️ **代码已修复，契约与测试缺失**——`service.py:788-802`：限流/配额命中改为记 INFO 日志并 return，响应 200 纯事实包（narrative=null）；interpret 保留 429（`service.py:151-152`），与上轮建议方向一致。但修复建议中"写入 APP-CONTEXT/ai-architecture 后再实现 03c"未做——两文档均无该降级行为描述（grep 无命中）；`test_narrative_report.py` 亦无 rate limit/quota 降级分支的任何用例 |

### 5.3 第 3 轮新登记问题（R-M4-17）

#### 🔴 R-M4-17（P1 · 交付）— HEAD 不自洽：纯净克隆下服务端整套测试收集失败、应用无法启动

- **ID：** R-M4-17
- **级别：** P1
- **类别：** 交付 / 版本完整性
- **状态：** 未修复
- **文件与位置：** HEAD `server/src/main.py:27,54`（`from src.modules.ai.routes import router`、`from src.modules.ai.provider import build_provider`）；`git ls-tree HEAD server/src/modules/ai/` 仅含 `service.py`；HEAD `server/tests/test_narrative_report.py:16-27`（import `fact_pack`/`narrative`）；`server/tests/conftest.py:40`（`from src.main import app`）
- **问题与触发条件：** 两段历史叠加：① `f71e771`（T-M3-02/03 提交）把含 ai 路由 import 的 `main.py` 提交入库，而 ai 模块当时全部 untracked → **HEAD 自该提交起已破损**；② `0bbd113`（R-M4-09 修复）只提交了 `service.py` + `test_narrative_report.py`，二者 import 的 `.prompts`/`.provider`/`.schemas`/`.fact_pack`/`.narrative`/`__init__.py` 仍 untracked，破损未修复反而新增了不可独立运行的提交。
- **证据或复现命令：** `git worktree add <tmp> HEAD && cd <tmp>/server && python -m pytest tests/test_narrative_report.py -q` → **collection error**（本轮实测）；`conftest.py` import `src.main` 失败将连带整个套件。
- **影响：** 纯净克隆无法启动服务端、无法运行任何服务端测试；远端 CI 若补上 server job（R-M4-01/04 要求）将全红；`0bbd113` 声称的修复在 HEAD 上不可独立复验（30 passed 只在含未提交文件的工作树成立）。这是 R-M4-01"产物未提交"的恶化形态：从"交付缺失"升级为"已交付树破损"。
- **修复建议：** 单次原子提交补齐：`server/src/modules/ai/` 全目录、`test_fact_pack.py`、`statistics` 的 `YearMonth` 更名、`models/`/`migrations/`/`config`/`main.py` 相关改动；提交前在纯净 worktree（或 clone）中跑通 `pytest tests/` 全量收集。若短期内无法整树入库，应先 revert `main.py` 的 ai import 恢复 HEAD 自洽。
- **复验方法：** 全新 clone HEAD → `pytest tests/` 收集零 error；`uvicorn src.main:app` 可启动。

#### 关联既有问题（不重复建项）

- **R-M4-01（P1 · 流程）：** 未提交问题持续且已具体恶化为 R-M4-17；本轮 `test_attachments` 4 个失败同样源于工作树内未提交的其他任务改动，佐证"未原子提交"对全量门禁的系统性干扰。
- **R-M4-04（P2 · CI）：** ruff 依赖缺口持续，lint 门禁仍无法本地执行；远端 CI 的 server job 仍只存在于未提交的 `ci.yml` 工作树改动中。
- **R-M4-06（P3 · 审计）：** 与 R-M4-10 同族，均未修复。

### 5.4 门禁结论（第 3 轮）

**实现层：不通过（较第 2 轮有实质进展）。** R-M4-09（P1）已真实关闭并有测试；R-M4-15 代码行为已按建议修正。但 R-M4-08（P1）只修了半程——响应内部口径统一了，与 `data-model.md`/statistics 的权威冲突和对账不变量破坏原样存在，且修复决策（允许负余额 vs 回滚 clamp）仍未按"文档前置"落定；R-M4-10/11（P2）无任何动作。

**交付层：不通过且恶化。** 新增 R-M4-17（P1）：HEAD 在纯净克隆下整套服务端测试收集失败、应用无法启动，`0bbd113` 为不可独立复验的部分提交；R-M4-01/04 持续。

**状态处置：** `production-execution-plan.md` 中 T-M4-03 及 03a/03b 状态行已按本轮证据更新（工作区内更新，不随本审查文档提交，避免夹带实现方未提交改动）。

**建议承接顺序：** ① R-M4-17 原子提交恢复 HEAD 自洽（同时解决 R-M4-01 在本任务范围的形态）→ ② R-M4-08 口径决策（改 `data-model.md` + statistics 放开负值，或事实包回滚 clamp，文档先行）→ ③ R-M4-10/11（降级路径记 AIUsage + parity 测试）→ ④ R-M4-15 契约写入文档并补测试；R-M4-12/13/14 可随 T-M4-03c 一并处理。

---

## 六、第 2 轮审查（历史保留，2026-09-06）

> 以下为第 2 轮审查原文。其登记问题（R-M4-08 ~ R-M4-15）的最新状态以本文件第二节（第 4 轮核验）为准；文中"本轮"均指第 2 轮。

### 6.1 实测结果（第 2 轮）

| 检查项 | 命令 | 结果 |
|---|---|---|
| 任务级测试 | `pytest tests/test_fact_pack.py tests/test_narrative_report.py -q` | **28 passed**（13+15，与实现总结声称一致） |
| 全量服务端测试 | `pytest tests/ --tb=short -q` | **366 passed, 1 skipped, 1 error**；error 为 `test_stats_exports.py::test_startup_cleanup_removes_stale_files` 的 Windows 临时目录 `PermissionError`（fixture setup 阶段，环境问题，与 T-M4-03 无关，单独重跑同样 error） |
| Lint（CI 门禁） | `ruff check src/ tests/` | **无法执行**——本机未安装 ruff 且 `pyproject.toml` 未声明（沿用 R-M4-04，未重复建项） |
| 远端 CI | 本审查文档提交 `f6aee16` 触发 run `34024846526` | **failure**——但失败的是 `Client Lint & Test` 与 `Android Compile & JVM Test` 两个既存失败 job（与上一次 T-M4-01-review 文档提交 `34024129435` 失败模式完全一致；根因 `:app:buildWeb FAILED`，纯文档提交不可能触发）。**关键：HEAD 上 committed 的 `ci.yml` 只有 client + android 两个 job，无 server job**（`git show HEAD:.github/workflows/ci.yml` 确认）——带 ruff+pytest 的 server job 仅存在于 `.github/workflows/ci.yml` 的**未提交工作树改动**中，故远端 CI 对 T-M4-03 服务端代码零覆盖（强化 R-M4-01/R-M4-04） |

> 注：`T-M4-03-final-summary.md` 写"297 个集成测试全部通过"，实测当前为 366 passed（数字过期，见 R-M4-13）。

### 6.2 第一轮审查问题的修复核验（第 2 轮核验）

第一轮审查（本文档第七节，历史保留）提出 5 项问题；实现方在 `T-M4-03b-code-review-improvements.md` 中声称处置如下。第 2 轮逐项对照代码核验：

| 原问题 | 声称处置 | 核验结果 |
|---|---|---|
| P2 `build_context_dict`/`_build_lookup` 代码重复 | 已重构复用 | ✅ **属实**——`narrative.py:151-168` `_build_lookup` 复用 `build_context_dict` 后过滤数值字段 |
| P2 `_find_undeclared_numbers` 将 lookup 值全部视为已声明 | 已文档化 | ✅ **属实**——`narrative.py:180-190` docstring 明确说明设计意图与年份误报局限 |
| P3 `balance = max(0, ...)` 掩盖赤字 | 已修复为允许负余额 | ⚠️ **部分修复，且引入新 P1**——顶层 balance 已放开（`fact_pack.py:205-206`），但 `trend` 内 balance 仍 `max(0, ...)`（`fact_pack.py:497`）；且与权威口径 `data-model.md` 冲突 → 立为 **R-M4-08**（第 3 轮：trend 已放开，权威口径冲突仍在） |
| P3 缺 mock provider 端到端叙述测试 | 🚫 取消（复杂度高） | ❌ **风险已兑现**——正因没有 mock provider 测试，"同数据不重复调 LLM"的验收缺口（R-M4-09）与降级路径漏计费用（R-M4-10）未被任何测试暴露；现有"缓存行为"测试在无 provider 下恒过（第 3 轮：R-M4-09 修复时已补 2 个 mock provider 缓存测试） |
| P3 年份误报 | 已文档化 | ✅ **属实**——同第 2 项 docstring |

### 6.3 第 2 轮登记问题原文（R-M4-08 ~ R-M4-15）

> 各项最新状态见本文件第二节核验表。以下为第 2 轮登记原文（其中 R-M4-09 状态字段为第 2 轮文档提交后补记的"已修复"）。

#### 🔴 R-M4-08（P1 · 正确性/契约）— balance 口径与权威数据模型冲突，破坏对账不变量

- **ID：** R-M4-08
- **级别：** P1
- **类别：** 正确性 / 契约一致性
- **状态：** 未修复
- **文件与位置：** `server/src/modules/ai/fact_pack.py:205-206`（顶层 balance 未 clamp）、`fact_pack.py:497`（trend balance 仍 clamp）、对照 `server/src/modules/statistics/service.py:156`、`statistics/schemas.py:27,38`（`Field(ge=0)`）、`docs/architecture/data-model.md:8`
- **问题与触发条件：** `data-model.md` 明文规定："`MonthlyStats.balance = max(0, totalIncome − totalExpense)`。`MoneyCents` 禁止负值，赤字不存入 balance，由展示层推导。客户端统计、服务端统计与导出必须使用同一口径"。第一轮审查后，实现方将事实包顶层 balance 改为允许负值（`balance = total_income - total_expense`），但：① 未先修改 `data-model.md` 与 statistics 口径（违反"文档前置"）；② statistics API 仍返回 clamp 后余额，赤字月份 `/ai/report.balance ≠ /statistics/monthly.balance`，直接破坏 T-M4-03a 验收标准"对账不变量：事实包数字 == 统计 API 输出"；③ 同一响应内 `trend[-1].balance`（clamp）与顶层 `balance`（不 clamp）在赤字月互相矛盾。
- **证据或复现命令：** 代码对照即可确认（上行文件:行号）。另：`T-M4-03b-code-review-improvements.md` 第 3 节声称"与 StatisticsService 的设计保持一致（该服务也使用相同口径）"——**该声明不实**，statistics 从未移除 `max(0, ...)`。
- **影响：** 客户端报表页若同时消费统计 API 与 /ai/report，赤字月份两处余额数字不一致，用户可见矛盾；对账不变量失守。
- **修复建议：** 二选一，且必须文档先行：(a) 回滚为 `max(0, ...)`，与 data-model.md/statistics 对齐，赤字信息由 LLM 通过 `total_income/total_expense` 对比表达；(b) 若产品确认放开负余额，先修订 `data-model.md`、`statistics`（含 `Field(ge=0)` 约束）与导出，再统一事实包顶层与 trend 两处口径。
- **复验方法：** 构造支出>收入的赤字月数据，断言 `/ai/report` 的 `balance`、`trend[-1].balance` 与 `/statistics/monthly` 的 `balance` 三者口径一致；新增回归测试。

#### 🔴 R-M4-09（P1 · 正确性/成本）— "同数据不重复调 LLM、不重复计费"未实现，文档声称已实现

- **ID：** R-M4-09
- **级别：** P1
- **类别：** 正确性 / 成本控制 / 文档失真
- **状态：** ✅ 已修复（2026-09-06，提交 `0bbd113`；第 3 轮复验通过，见第二节）
- **文件与位置：** `server/src/modules/ai/service.py:55-58`（叙述缓存模块变量）、`service.py:92-116`（缓存辅助函数）、`service.py:735-755`（缓存检查逻辑）、`service.py:868-874`（缓存存储逻辑）、`server/tests/test_narrative_report.py:301-464`（缓存行为测试）
- **问题与触发条件：** T-M4-03a 交接产物明确要求"数据哈希缓存（同数据不重复调 LLM、不重复计费）"，APP-CONTEXT T-M4-03 验收要求"数据未变不重复计费"。实际实现：只要 provider 存在，每次 `POST /ai/report` 都会调用 LLM 并产生计费；`data_hash` 仅用于事实包对象缓存，而该缓存在**全部 DB 查询完成之后**才检查（`fact_pack.py:236-240`），只节省 dataclass 构造。`narrative_cached` 恒为 False，routes/schemas 的 docstring 描述了不存在的行为。全仓库 grep 确认无其他叙述缓存实现。
- **修复方案：** 在 `service.py` 中添加叙述缓存机制（内存缓存，TTL 300s，最大 500 条目，与 FactPack 缓存同规格）。缓存键为 `(book_id, period, data_hash)`。缓存命中时：
  - 直接填充 `response.narrative`
  - 设置 `response.narrative_cached = True`
  - 不调用 provider、不记录 AIUsage
  - 记录 INFO 日志以便运维监控

  缓存未命中时：
  - 正常调用 provider 生成 narrative
  - 数字校验通过后，将 narrative 存入缓存
  - 设置 `response.narrative_cached = False`
  - 记录 AIUsage

  新增 mock provider 测试验证：
  - 同数据第二次请求 `provider.complete` 调用数为 0
  - `narrative_cached == True`
  - AIUsage 仅首次记录
  - 不同数据触发新的 provider 调用
- **复验方法：** ✅ 已通过（`pytest tests/test_narrative_report.py::TestNarrativeCache -v` → 2 passed）

#### 🟡 R-M4-10（P2 · 可靠性/审计）— 校验失败与降级路径不记录已消耗的 tokens，guest 配额可被绕过

- **ID：** R-M4-10
- **级别：** P2
- **类别：** 可靠性 / 成本审计
- **状态：** 未修复
- **文件与位置：** `server/src/modules/ai/service.py:722-757`（ProviderError 降级 return、校验失败 return 均不经过 `_record_usage`；仅 760 行成功路径记录）
- **问题与触发条件：** `_generate_narrative` 中 LLM 已被真实调用并消耗 tokens 后，若数字校验失败（`number_verification_failed`）或重试后仍 ProviderError，函数直接 return，响应仍以 200 提交（会话不回滚）。`interpret()` 的"失败路径不记录（会话回滚丢弃）"理由（`service.py:240-246` docstring）在此不成立。
- **证据或复现命令：** 代码路径分析；mock provider 返回篡改数字的输出，断言 AIUsage 行数——当前实现为 0 行。
- **影响：** ① provider 成本黑洞：校验失败率越高，账外消耗越大，且 T-M4-03b 交接产物明确要求"AIUsage 记录"；② `_check_daily_quota` 以 AIUsage 行计数，反复触发校验失败的 guest 用户可无限消耗 tokens 而不耗尽配额（与 R-M4-06 同族但路径不同：R-M4-06 是重试成功路径漏计首次 tokens，本项是降级路径整体不计）。
- **修复建议：** 只要 provider 实际返回过 result，无论校验是否通过都调用 `_record_usage`；校验失败时可另加标记字段/日志区分。
- **复验方法：** mock provider 输出篡改数字 → 断言 AIUsage 新增 1 行且响应 narrative 为 null。

#### 🟡 R-M4-11（P2 · 测试）— 对账不变量"事实包数字 == 统计 API 输出"无自动化守卫

- **ID：** R-M4-11
- **级别：** P2
- **类别：** 测试 / 可维护性
- **状态：** 未修复
- **文件与位置：** `server/tests/test_fact_pack.py`（全文无与 /statistics 的对比断言）；`fact_pack.py:432`（仅从 statistics 导入 `_YearMonth`，聚合逻辑为独立重实现）
- **问题与触发条件：** T-M4-03a 最低验证明确要求"对账不变量：事实包数字 == 统计 API 输出"。事实包没有复用 `StatisticsService`（交接产物写"复用 statistics 模块"，实际只复用了 `_YearMonth` SQL 函数），月度聚合/趋势/分类占比均为平行实现，且没有任何测试将两者输出对账。R-M4-08 的口径漂移正是该守卫缺失的直接实例。
- **证据或复现命令：** `grep -n "statistics\|/stats" server/tests/test_fact_pack.py` → 无结果。
- **影响：** 两套聚合实现随时间漂移无人发现；对账不变量仅存在于文档。
- **修复建议：** 新增 parity 测试：同一账本同一 period 下 `/ai/report` 的 total_income/total_expense/balance/分类合计/趋势与 `/statistics` 对应端点逐项相等（参照 `test_ai_proposal_parity.py` 的模式）；或让 FactPackService 直接调用 StatisticsService。
- **复验方法：** parity 测试入 CI 并通过；人为改动任一侧口径应使测试变红。

#### 🟢 R-M4-12（P3 · 正确性）— 事实包缓存在 TTL 内可返回陈旧的非哈希字段

- **ID：** R-M4-12
- **级别：** P3
- **类别：** 正确性
- **状态：** 未修复
- **文件与位置：** `server/src/modules/ai/fact_pack.py:134-166`（hash 覆盖范围）、`fact_pack.py:236-240`（缓存检查在全部查询之后）
- **问题与触发条件：** `data_hash` 不覆盖 `transaction_count`、最大单笔的 `id/note/biz_date`、分类名称。TTL（300s）内用户仅修改交易备注/分类名（金额不变）或发生"总额不变的拆分合并"时，hash 相同 → `build()` 丢弃刚查出的新数据，返回缓存中的旧 FactPack，LLM 与响应看到陈旧 note/count。
- **证据或复现命令：** 代码路径分析；复现需两次请求间改备注不改金额（现有测试未覆盖）。
- **影响：** 5 分钟窗口内的小概率数据陈旧；金额类字段不受影响（均入 hash）。
- **修复建议：** 缓存命中仅用于"是否跳过 LLM 调用"的判定（配合 R-M4-09 的叙述缓存），FactPack 本身始终用当次查询结果构造；或将全部字段纳入 hash。
- **复验方法：** 同数据改备注后再次请求，断言响应中 note 为新值。

#### 🟢 R-M4-13（P3 · 文档）— 交付文档与实现多处不符

- **ID：** R-M4-13
- **级别：** P3
- **类别：** 文档
- **状态：** 未修复
- **文件与位置与逐条事实：**
  1. `T-M4-03-final-summary.md`"性能优化"节：缓存"最大缓存条目：1000"——代码为 `_MAX_CACHE_ENTRIES = 500`（`fact_pack.py:98`）；
  2. `T-M4-03-final-summary.md`：297 个集成测试——实测 366 passed（第 2 轮第一节）；
  3. `T-M4-03b-code-review-improvements.md` 第 3 节："与 StatisticsService 的设计保持一致（该服务也使用相同口径）"——不实（statistics 仍 clamp，见 R-M4-08）；
  4. 第一轮审查文档（本文件第七节）"缓存策略 … LRU 淘汰"——实际按插入时间 FIFO 淘汰，读取不刷新时间戳（`fact_pack.py:116-121`）；
  5. `test_narrative_report.py` 文件头声称含"POST /ai/report — 端到端测试"——该文件中不存在任何 HTTP 测试；
  6. `T-M4-03-final-summary.md` 验证清单将"同数据不重复计费"相关的缓存机制勾选为通过——与 R-M4-09 冲突。
- **影响：** 与 R-M4-02（closure 报告失真）同类：交付文档可信度受损，后续任务依据失真状态决策。
- **修复建议：** 随 R-M4-08/09 修复一并更正上述条目；数字类声称以可复跑命令为准。
- **复验方法：** 逐条对照代码与测试输出。

#### 🟢 R-M4-14（P3 · 效率/提示词）— prompt 要求输出 `highlights` 但服务端丢弃；"本月"措辞对历史期间不准确

- **ID：** R-M4-14
- **级别：** P3
- **类别：** 效率 / 提示词设计
- **状态：** 未修复
- **文件与位置：** `server/src/modules/ai/prompts.py:64`（要求输出 highlights 数组）、`service.py:764`（仅取 narrative）；`prompts.py:58` 与 `service.py:706`（"本月"/"我本月（{period}）"措辞）
- **问题与触发条件：** ① LLM 按模板生成的 `highlights` 被服务端丢弃，白白消耗 completion tokens；且 `_find_undeclared_numbers` 只扫描 `narrative` 字段，若未来把 highlights 透出将处于校验盲区。② 请求历史月份（如 2026-08 在 9 月请求）时，系统提示与用户输入均称"本月"，叙述可能产生错误时间指代。
- **影响：** token 浪费（小）；历史期间叙述措辞错位（体验问题）。
- **修复建议：** 要么从模板删除 highlights，要么纳入响应与校验；用户输入措辞改为"{period} 月度"。
- **复验方法：** 请求上月报表，检查叙述时间指代；观察 completion_tokens 下降。

#### 🟢 R-M4-15（P3 · 可靠性/契约）— 限流触发时整个 /ai/report 返回 429，而非降级为纯事实包；无 provider 时事实包查询不限流

- **ID：** R-M4-15
- **级别：** P3
- **类别：** 可靠性 / 契约
- **状态：** 未修复
- **文件与位置：** `server/src/modules/ai/service.py:697-699`（`_check_rate_limit`/`_check_daily_quota` 在 `_generate_narrative` 内、事实包构建之后抛出 AppException）
- **问题与触发条件：** 响应契约定义了"无 LLM 或校验失败 → narrative null 的 200 降级"，但限流/配额命中时整个请求 429，事实包数据（不产生 LLM 成本）也被扣留。另：provider 未配置时限流检查不会执行，事实包的 6+ 次 DB 聚合查询可被无限刷（与 /statistics 负载相当，风险低）。
- **影响：** 客户端需要同时处理"429 → 本地降级"与"200 + narrative null"两条降级路径；T-M4-03c 实现前契约应明确统一。
- **修复建议：** 倾向：限流命中时仍返回 200 纯事实包（narrative=null），把 429 保留给 interpret 这类纯 LLM 端点；无论采用哪种，写入 APP-CONTEXT/ai-architecture 后再实现 03c。
- **复验方法：** 打满限流后请求 /ai/report，断言行为与最终契约一致。

### 6.4 门禁结论（第 2 轮）

**实现层：不通过。** T-M4-03a/03b 的核心安全链路（事实包隔离、data_references 校验、未声明数字检测、失败降级、所有权校验）代码与测试属实且 28 用例全绿；但存在 2 项 P1：R-M4-08 破坏权威口径与对账不变量（且修复声明不实）、R-M4-09 使"数据未变不重复计费"验收落空（docstring 声称了不存在的行为）。另有 P2 两项（R-M4-10/11）。

**交付层：不通过。** R-M4-01（产物未提交、无远端 CI）同样适用于本任务；交付文档存在 R-M4-13 所列失真。

**状态处置：** `production-execution-plan.md` 中 T-M4-03a/03b 的"✅ 已完成"已依据本轮证据改为复审不通过标记（工作区内更新；该文件含实现方未提交改动，不随本审查文档提交，避免夹带）。

**建议承接：** `修复 T-M4-review 中 R-M4-08 ~ R-M4-11`（P1/P2 四项），R-M4-12~15 可随 T-M4-03c 一并处理。

---

## 七、第一轮审查（历史保留，2026-09-06 早于第 2 轮）

> 以下为第一轮审查原文。其 5 项问题的处置核验见本文件第 6.2 节；其中"缓存策略 LRU"表述有误（实为 FIFO，见 R-M4-13）。

### 总体评价

架构设计清晰：事实包模式（Fact Pack）作为 LLM 的**唯一数据源**，数字校验 + 安全降级构成完整的安全链路。三层防护（事实包隔离 → `data_references` 校验 → 未声明数字检测）设计合理，测试覆盖了核心路径。

### 架构优点

| 维度 | 评价 |
|---|---|
| **安全设计** | 三层防护链完整：LLM 只接触事实包 → 每个数字必须声明引用 → 校验失败丢弃叙述。安全降级不暴露内部错误。 |
| **数据溯源** | SHA-256 哈希 + 确定性重建，确保相同数据产生相同哈希，避免重复的 LLM 调用。 |
| **缓存策略** | 内存缓存 TTL 300s + 500 条目上限 + LRU 淘汰，首次发布够用。 |
| **跨数据库兼容** | 复用 `_YearMonth` 自定义 SQL 函数，SQLite 和 PostgreSQL 均支持。 |
| **测试质量** | 28 个测试覆盖了哈希稳定性、事实包生成、缓存行为、趋势跨年、校验逻辑、上下文构建。 |

### 发现问题（原始编号未使用 R-M4 序列）

1. 🟡 P2 — `build_context_dict` 和 `_build_lookup` 存在代码重复（`narrative.py`）→ 第二轮核验：已修复。
2. 🟡 P2 — `_find_undeclared_numbers` 将 lookup 全部值加入"已声明"集合，次级检查有效性降低 → 第二轮核验：已文档化（有意设计）。
3. 🟡 P3 — 余额计算 `max(0, ...)` 掩盖赤字场景（`fact_pack.py`）→ 第二轮核验：部分修复且引入 R-M4-08。
4. 🟢 P3 — 缺少 mock provider 的端到端叙述生成测试 → 处置：实现方取消；第二轮核验：风险兑现（R-M4-09/10 未被暴露）。
5. 🟢 P3 — 年数字误报可能性（正则 `\d{3,}` 匹配年份）→ 第二轮核验：已文档化。

### 安全审计（第一轮，后续轮次未推翻）

| 检查项 | 结果 |
|---|---|
| LLM 数据隔离 | ✅ 只看到事实包，不接触原始 DB |
| 数字溯源验证 | ✅ `data_references` 逐一比对 |
| 校验失败降级 | ✅ 丢弃叙述，不返回部分数据 |
| 所有权校验 | ✅ `FactPackService.build()` 验证 book 所有权 |
| 速率限制 | ✅ 叙述生成路径受 `_check_rate_limit` 和 `_check_daily_quota` 保护（429 行为契约问题见 R-M4-15；第 3 轮：代码已改为 200 降级，契约待文档化） |
| SQL 注入 | ✅ 所有查询使用 SQLAlchemy ORM 参数化查询 |
| 安全事件日志 | ✅ 校验失败记录 `WARNING` 级别日志，含 `number_verification_failed` 标签 |
