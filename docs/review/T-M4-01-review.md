# T-M4-01 审查报告：服务端 AI Gateway（重新审查）

- **审查日期：** 2026-09-06
- **审查模式：** Review（只读；不修改项目代码/测试/配置）
- **审查对象：** T-M4-01 子任务补充收口后的实现结果（依据 `T-M4-01-closure.md`）
- **基线：** HEAD `f71e771` + 工作树未提交的 M4 改动
- **范围（包含）：** `server/src/modules/ai/`（01a/b/c/d 相关部分）、`server/src/common/config.py`（AI 段）、`server/src/main.py`（AI 启动段）、`server/src/models/ai.py`、AI 相关测试、`docs/adr/ADR-003-webview-ai-channel.md`、`docs/architecture/ai-architecture.md`（WebView 通道段）、`T-M4-01-closure.md`
- **范围（排除）：** T-M4-02（`validate_proposal`/`execute_proposal` 业务逻辑、`ProposalIdempotency`）、T-M4-03（`report`/`narrative`/`fact_pack`）——仅在其与 01 共用文件时顺带核对，不展开；客户端 `app/` AI 代码；真实 DashScope 冒烟（外部阻塞）
- **依据：** `docs/milestones/production-execution-plan.md` 第 592–599 行（T-M4-01a–d 验收标准）、`docs/APP-CONTEXT.md` §6.4/§10.3、`docs/architecture/ai-architecture.md`、`docs/engineering/通用任务执行规范.md`

> **说明：** `service.py`/`routes.py`/`schemas.py` 同时承载 01/02/03 三个子任务的代码。本报告只对 T-M4-01 职责部分（interpret、限流、配额、用量审计、超时语义、降级、脱敏日志、provider 适配、prompt registry、提案校验器平价、`/ai/interpret` 端点与鉴权、ADR-003）下结论。

---

## 一、验证命令与实测结果

| 命令 | 结果 | 备注 |
|---|---|---|
| `pytest tests/ --tb=line -q` | **364 passed, 1 skipped, 1 error** | error = `test_stats_exports.py::...test_startup_cleanup_removes_stale_files`，`PermissionError [WinError 5]` 于 `C:\Users\admin\AppData\Local\Temp\pytest-of-admin`；skip = 同文件 `:581 POSIX permission bits only`。二者均属 exports 模块（T-M2-05）Windows 环境限制，与 AI Gateway 无关 |
| `pytest tests/test_migrations.py -q` | **12 passed** | 与 closure 报告"2 项失败"声明矛盾，详见 R-M4-02 |
| `pytest tests/test_auth.py::TestProductionGuards tests/test_ai_gateway.py::TestSlowHint tests/test_ai_proposal_parity.py -q` | **26 passed** | closure 报告点名的新增测试均存在且通过 |
| 各文件收集计数 | config 14 / auth 40 / main 6 / ai_gateway 46 / parity 17 / validation 16 = **139** | 与 closure 报告逐文件明细不符，详见 R-M4-02 |
| `ruff check src/ tests/`（CI 门禁） | **无法执行** | 本机未安装 ruff；且 ruff 未在 `pyproject.toml` 声明，详见 R-M4-04 |

**只读边界确认：** 本次审查未修改任何项目代码、测试、配置、依赖或迁移；仅运行只读检查与测试，并新建本审查文档。

---

## 二、验收标准实测对照

### T-M4-01a Provider 适配与模块骨架 — ✅ 达成

| 验收点 | 证据 | 结论 |
|---|---|---|
| provider adapter 接口 + DashScope（OpenAI 兼容，httpx async） | `provider.py`：`LLMProvider` Protocol、`DashScopeProvider.complete()` POST `{base}/chat/completions`，`httpx.AsyncClient` | ✅ |
| Settings 扩展（模型/超时/重试，key 仅环境变量） | `config.py:107-147`：`ai_provider_*`、`ai_timeout_seconds`、`ai_rate_limit_per_minute` 等 | ✅ |
| **production 缺 key 拒绝启动** | `config.py:176-180` `_check_production_safety` 仅 `env=="production"` 校验 `ai_provider_api_key`；`test_auth.py:399 test_ai_key_missing_rejected_in_production`（负例）+ `:411 test_production_accepts_secure_config`（正例） | ✅ 实测通过 |
| dev/staging 缺 key 不阻塞、运行时 503 降级 | `main.py:52-60` lifespan 警告；`service.py:117-121` provider None → 503 `ai_unavailable`；`test_ai_gateway.py:361 test_no_provider_configured_returns_503` | ✅ |
| mock provider | `provider.py:70` transport 注入点；`test_ai_gateway.py:54 FakeProvider` | ✅ |

### T-M4-01b Prompt registry 与服务端提案校验 — ✅ 达成

| 验收点 | 证据 | 结论 |
|---|---|---|
| Prompt registry（版本化、按任务） | `prompts.py`：`PromptTemplate` dataclass、`REGISTRY`、`get_template`、`render_messages` | ✅ |
| LLM 输出 JSON 解析（容忍 markdown fence） | `service.py:214-233 _parse_json_output`；`test_ai_gateway.py:240 test_markdown_fenced_json_parsed` | ✅ |
| Python ActionProposal 校验器（Draft-07 + `format: date` 断言） | `validator.py`：`Draft7Validator` + `FormatChecker`，`_is_valid_biz_date` 经 `datetime.date` 往返拒绝 `2025-02-30` | ✅ |
| 三校验器同 fixture 同结论（共享 fixtures 平价） | `test_ai_proposal_parity.py`：16 fixture（7 valid + 9 invalid）+ `test_all_fixtures_discovered` 漂移守卫 | ✅（轻量平价，见观察 O-1） |
| 非法 JSON 重试一次、仍失败降级（§6.4） | `service.py:129-159`（`MAX_ATTEMPTS=2`）；`test_ai_gateway.py:399/410/419` | ✅ |

### T-M4-01c 限流、用量审计与降级 — ✅ 达成（1 项 P3 审计缺口）

| 验收点 | 证据 | 结论 |
|---|---|---|
| 用户级限流 | `service.py:179-195`（内存桶，60s 窗口）；`test_ai_gateway.py:318 test_rate_limit_rejects_eleventh_call`（429 `ai_rate_limited`）、`:327` 按用户隔离 | ✅ |
| 配额（guest 日配额，active 无上限，按用户时区日起算） | `service.py:197-210`、`local_day_start_utc`；`test_ai_gateway.py:335/344/351`（含昨日行不计、北京时区 16h UTC） | ✅ |
| AIUsage append-only 审计（模型/tokens/requestId） | `service.py:237-263`、`models/ai.py:48-73`；`test_ai_gateway.py:254 TestUsageRecording`（成功记录、失败不记录、成本计算、requestId 截断 64） | ✅（重试 token 漏计，见 R-M4-06） |
| 超时语义（12s 慢速提示 / 25s 取消） | `service.py:64 SLOW_HINT_SECONDS`、`:313-339 _log_slow_if_needed`；`test_ai_gateway.py:503 TestSlowHint`（慢/快两路）；provider 超时 `retryable=False`（`provider.py:94-98`），2×10s<25s 预算成立 | ✅ |
| provider 故障 → 降级响应 | `service.py:137-159`；`test_ai_gateway.py:360 TestDegradation`（503/重试/超时不重试/隐藏模型输出） | ✅ |
| 脱敏日志 + requestId 贯穿 | `service.py:265-311`（仅元数据）；`test_ai_gateway.py:440 TestRedactedLogging`（"十二块五"/amountCents 不入日志、provider 错误用枚举 reason） | ✅ |

### T-M4-01d 网关 API 与端到端收口 — 🔨 在途（与 closure 报告一致）

| 验收点 | 证据 | 结论 |
|---|---|---|
| `POST /ai/interpret` 端点 + Bearer 鉴权 | `routes.py:33-48`（`Depends(get_current_user)`）；`test_ai_gateway.py:200 test_requires_auth`（401） | ✅ |
| 端点命名 `/ai/interpret`（非 `/ai/parse`） | `routes.py:33`；执行计划第 599 行已对齐 | ✅ |
| 端到端测试（mock provider） | `test_ai_gateway.py` 经 TestClient 走真实路由 + FakeProvider | ✅ |
| ADR-003 WebView AI 调用通道 | `docs/adr/ADR-003-webview-ai-channel.md`（决策表 + 时序图 + 拒绝 3 替代方案 + 后果） | ✅ 设计层面达成（Kotlin `AiGatewayClient`/桥 v2 在 ADR 中明列为 ⚠️ 未实现后果，属下游 T-M4-02c/03c） |
| 真实 DashScope 冒烟（D-EXEC-04） | 未执行 | 🔨 外部阻塞（密钥待确认），closure 如实记录 |
| 产物密钥扫描零命中 | 未执行 | 🔨 归属 T-M5-02c，closure 如实记录 |

**实现层小结：** 01a/01b/01c 验收点均有代码与测试支撑，实测全绿；01d 端点/鉴权/ADR 落地，仅余两项如实记录的外部/跨任务阻塞。closure 报告"实现完成度 ~95%"在**实现层面**基本属实。问题集中在**交付治理**与**报告事实准确性**，见下。

---

## 三、发现问题

### 🔴 R-M4-01（P1 · 流程/交付）— T-M4-01 实现全量未提交，完成门禁未达成

- **状态：** 未修复
- **文件与位置：** `git status`（基线 HEAD `f71e771`）
- **问题与触发条件：** T-M4-01 的全部产物处于未提交状态——`server/src/modules/ai/`（整目录 untracked）、`server/tests/test_ai_gateway.py`/`test_ai_proposal_parity.py`/`test_ai_proposal_validation.py`（untracked）、`docs/adr/`（整目录 untracked，含 ADR-003）、`server/src/common/config.py`/`main.py`/`models/ai.py`（modified 未暂存）、`T-M4-01-closure.md`（untracked）。
- **证据或复现命令：** `git status` 显示上述路径在 "Changes not staged" / "Untracked files"；`git log --oneline` 最新提交为 `f71e771`（T-M3-02/03），无任何 M4 提交。
- **影响：** 违反 `通用任务执行规范.md` 开发实现模式"完成门禁"（需"当前任务改动已单独提交并推送"+"远端 CI 已通过"）。后果：① 零持久性（`git clean -fd`/`checkout` 可整体丢失）；② 无远端 CI 验证；③ 与 T-M4-02a/b/d、T-M4-03 的未提交改动混杂于同一工作树，后续难以按任务干净拆分提交。closure 报告以"完成/准完成"框定，但该状态未达门禁。
- **修复建议：** 按任务精确路径分别提交 M4 各子任务产物（先 01，再 02/03），提交信息含 Task ID，推送后确认远端 CI。若团队有意将 M4 批量延后提交，应在 closure 报告与里程碑状态中明确标注"实现完成、尚未提交"，不得以"完成"暗示门禁已过。
- **复验方法：** `git log --oneline | grep T-M4-01` 出现提交；`git status` 中 AI 模块路径不再为 untracked；远端 CI 对应 commit 通过。
- **备注：** 此为 M4 里程碑范围内的共性状态（非 01 独有），但直接决定 T-M4-01 能否合规判定为"完成"。

### 🟡 R-M4-02（P2 · 文档）— `T-M4-01-closure.md` 测试事实失真

- **状态：** 未修复
- **文件与位置：** `T-M4-01-closure.md` 第 42–67、79–86 行
- **问题与触发条件：** 报告的测试结果与"未处理项"与当前仓库实测不符：
  1. 报告称"完整测试套件：352 passed，1 skipped"；实测 **364 passed, 1 skipped, 1 error**。
  2. 报告"未处理项 #3"称 `test_migrations.py` 有"2 项失败……需独立 PR 修复迁移表计数（13→14）与 migration 文件"；实测 `test_migrations.py` **12 passed**，且 `test_migrations.py` 已改为 `test_14_tables_registered`（断言 ==14）、迁移列表已含 `0004`–`0008`、新增 `test_proposal_idempotency_migration_chains_onto_0007`，`0007_ai_usage_index.py`/`0008_proposal_idempotency.py` 均已存在。该问题已被 T-M4-02b 的工作树改动解决，报告未更新。
  3. 逐文件明细失真：报告 `test_ai_gateway.py 66 passed`（实测收集 **46**）、`test_auth.py 32`（实测 **40**）、`test_config.py 12`（实测 **14**）；报告明细表自求和 = 149，与其标题"139 全绿"自相矛盾（实测 6 文件收集合计恰为 **139**，即标题对、明细错）。
- **证据或复现命令：** `pytest tests/ -q`（364/1/1）；`pytest tests/test_migrations.py -q`（12 passed）；`git diff server/tests/test_migrations.py`（已含 14 表 + 0008 链测试）；逐文件 `pytest --co -q` 计数。
- **影响：** 收口报告作为交付记录不可信；"未处理项"误导后续接手者去做已被解决的工作（重复修 migration 测试）；测试计数虚高（66 vs 46）夸大覆盖。
- **修复建议：** 以实测重写报告测试段：364 passed / 1 skipped / 1 error（error 标注为 Windows 环境、exports 模块、非 AI）；删除"未处理项 #3"或改注"已由 T-M4-02b 工作树改动解决（test_migrations 12 passed）"；逐文件计数订正为 14/40/6/46/17/16。
- **复验方法：** 报告数字与 `pytest tests/ -q` 输出逐项一致。

### 🟡 R-M4-03（P2 · 契约/文档）— `/ai/interpret` 返回未校验最小解析，与权威文档"AI 只返回经 Schema 校验的 ActionProposal"不一致

- **状态：** 未修复
- **文件与位置：** `server/src/modules/ai/schemas.py:26-32`（`AIInterpretResponse.output: Any`）、`service.py:110-175`（interpret 仅 `_parse_json_output` 校验"是 dict"）、`prompts.py:31-51`（`LEDGER_PARSE` 输出 type/amountCents/category/note/bizDate/confidence，注释明示"NOT yet an ActionProposal"）；对照 `docs/architecture/ai-architecture.md:5`、`production-execution-plan.md:599`（"文本 → ActionProposal"）
- **问题与触发条件：** 架构文档原则为"AI 只返回通过 JSON Schema 校验的 ActionProposal"，执行计划 01d 写"`POST /ai/interpret`（文本 → ActionProposal）"。但实现中 `/ai/interpret` 的 `output` 类型为 `Any`，服务端只断言其为 JSON 对象，**不对其做 ActionProposal schema 校验**；真正的 ActionProposal 由客户端组装（`AIValidateProposalRequest` docstring："Client sends its assembled ActionProposal"），再经 `/ai/validate` + `/ai/execute` 校验。
- **证据或复现命令：** 阅读上述文件；`schemas.py:31` 注释 "json tasks → parsed dict"；`prompts.py:30` "Output is a minimal parse result, NOT yet an ActionProposal (T-M4-02)"。
- **影响：** 文档与实现描述的是两套契约。**写库安全边界未被破坏**（任何 DB 写入前必经 `/ai/validate` 的 schema+context 校验与 `/ai/execute` 的 TOCTOU 重校验），故风险低；但违反 AGENTS.md"架构/协议变更必须先改对应文档"，且 interpret 输出未受任何 schema 约束，与"AI 只返回经校验 ActionProposal"的安全原则字面冲突，易误导后续开发。
- **修复建议：** 二选一并先改文档：① 在 `ai-architecture.md` 与执行计划中把流程精确描述为"interpret → 最小解析（非提案）→ 客户端组装 ActionProposal → /ai/validate 校验 → /ai/execute"，明确 interpret 输出是中间表示；或 ② 若确需 interpret 直出提案，则对 `output` 施加 ActionProposal schema 校验并改 prompt。建议取 ①（与现有实现一致、改动最小）。
- **复验方法：** 文档描述与 `schemas.py`/`prompts.py`/`routes.py` 实际数据流一致。

### 🟡 R-M4-04（P2 · CI/工程）— 新增 server CI job 的 `ruff check` 缺少 ruff 依赖声明，门禁会失败

- **状态：** 未修复
- **文件与位置：** `.github/workflows/ci.yml`（工作树新增 server job，未提交）第 69–71 行；`server/pyproject.toml`（`[project.optional-dependencies].dev` 仅 pytest/pytest-asyncio/aiosqlite，无 ruff，无 `[tool.ruff]`）
- **问题与触发条件：** 工作树新增的 server job 执行 `pip install -e ".[dev]"` 后跑 `ruff check src/ tests/`。但 ruff 未在 `pyproject.toml` 任何依赖组声明，也无 pre-commit/独立配置；`pip install -e ".[dev]"` 不会安装 ruff。另：HEAD 版 `ci.yml` 只有 Client + Android 两个 job，**服务端模块（T-M2~T-M4）此前从未被远端 CI 覆盖**，本次是首次尝试加 server 门禁。
- **证据或复现命令：** `git show HEAD:.github/workflows/ci.yml | grep name:` → 仅 Client/Android；`grep -rn ruff server/pyproject.toml` → 无；本机 `python -m ruff` → `No module named ruff`。
- **影响：** 该 CI job 推送后 `ruff check` 步骤极可能以 "ruff: command not found"（exit 127）失败，使整个服务端门禁（含 T-M4-01）无法通过；本机亦无法执行 lint 等价检查。**推断说明：** 我无法执行远端 GitHub CI 以确证失败，结论基于"ruff 未声明为依赖"这一可核实事实 + ubuntu runner 默认不预装 ruff 的常识；若 runner 恰好预装则可能侥幸通过，但门禁不可复现仍属缺陷。
- **修复建议：** 将 `ruff` 加入 `pyproject.toml` 的 dev 依赖并固定版本，补 `[tool.ruff]` 配置；或改用 `pipx`/`uv tool` 显式安装。提交 ci.yml 前本地跑通 `ruff check`。
- **复验方法：** `pip install -e ".[dev]"` 后 `ruff --version` 可用；远端 CI server job 绿。
- **备注：** ci.yml 改动未提交、closure 报告 P0#4 又称"CI……不需改动"，二者矛盾；该改动归属需在提交前厘清（疑似 T-M4-02/03 或收口时引入）。

### 🟢 R-M4-05（P3 · 文档）— `validator.py` docstring 引用不存在的 `assembly.py`

- **状态：** 未修复
- **文件与位置：** `server/src/modules/ai/validator.py:13-15`
- **问题与触发条件：** docstring 称"The validator is intentionally separate from the assembly logic (`assembly.py`)"，但 `server/src/modules/ai/` 下无 `assembly.py`（提案组装实际在客户端，见 R-M4-03）。
- **证据或复现命令：** `ls server/src/modules/ai/` 无 assembly.py；`grep -rn assembly.py server/src` 仅此一处引用。
- **影响：** 误导读者去寻找不存在的模块；与 R-M4-03 同源（组装职责实际在客户端）。
- **修复建议：** 改为"组装在客户端完成（见 ADR-003 / `/ai/validate` 契约）"，删除对 `assembly.py` 的引用。
- **复验方法：** docstring 不再引用不存在的文件。

### 🟢 R-M4-06（P3 · 正确性/审计）— 重试路径下首次成功调用的 tokens 未计入 AIUsage 审计

- **状态：** 未修复
- **文件与位置：** `server/src/modules/ai/service.py:127-163`（interpret 循环 + `_record_usage` 仅在循环后调用一次）
- **问题与触发条件：** 当 attempt 1 成功到达 provider（已产生/计费 tokens）但返回非法 JSON，attempt 2 成功时，`_record_usage` 只记录 attempt 2 的 `result`，attempt 1 已消耗的 tokens 不入库。
- **证据或复现命令：** 阅读 `service.py:129-163`；`_record_usage(user, result, ...)` 的 `result` 为循环最终值。对照验收"AIUsage append-only 审计（tokens）"。
- **影响：** 用量审计在发生 JSON 重试时**少计真实 token 消耗**，成本看板（T-M5-05b）偏低。严重度低（重试罕见、非计费关键路径），但与"审计真实用量"目标有偏差。
- **修复建议：** 在每个成功拿到 `ProviderResult` 的 attempt 后累加 tokens（即使随后 JSON 解析失败），或单独记一条 attempt 级用量；保持 append-only 语义。
- **复验方法：** 构造"首次合法响应但非法 JSON、二次成功"用例，断言 AIUsage 累计 tokens == 两次 prompt+completion 之和。

### 🟢 R-M4-07（P3 · 流程）— 上一轮 T-M4-01 评审报告未落盘

- **状态：** 未修复
- **文件与位置：** `docs/review/`（缺 `T-M4-01-review.md`）；`T-M4-01-closure.md:6,8-10` 引用"T-M4-01 评审报告（97 项测试基线）"
- **问题与触发条件：** closure 报告基于一份"T-M4-01 评审报告"（给出 ~85–90% 完成度与 P0–P3 清单），但 `docs/review/` 中无对应文件（仅有 T-M4-00 系列与 T-M4-03）。该轮评审似仅在对话内进行，未按 Review 模式步骤 6 写入 `docs/review/`。
- **证据或复现命令：** `ls docs/review/ | grep T-M4-01` → 无；`grep -rn "R-M4-" docs/` → 无（M4 审查文档亦未使用规范要求的 `R-Mx-nn` 连续编号）。
- **影响：** 评审结论与问题清单缺乏可追溯落盘记录；closure 的"97 项基线"无法回溯核对；M4 审查文档整体未遵循 `R-Mx-nn` 编号规范（本报告起用 R-M4-nn 纠正）。
- **修复建议：** 后续 T-M4 各轮审查统一落盘 `docs/review/T-M4-0x-review.md` 并使用 `R-M4-nn` 连续编号；如上一轮评审清单仍可还原，补归档。
- **复验方法：** `docs/review/` 含对应文件且编号连续。

---

## 四、观察项（非缺陷，记录备查）

- **O-1 平价测试为"共享 fixture + 镜像期望"，非实时跨语言校验。** `test_ai_proposal_parity.py` 不实际运行 ajv/TS，而是把 TS 结论硬编码进 `CASES` 表；`test_all_fixtures_discovered` 只防 fixture 文件增删，不防 TS 校验逻辑漂移。验收"三校验器同 fixture 同结论"在字面上满足（两侧各自对共享 fixture 断言相同结论），但若 TS 校验器逻辑单方面变更，本测试不会报警。可接受为 v1 轻量方案。
- **O-2 限流桶在 no-provider 503 路径仍计入。** `interpret()` 先 `_check_rate_limit`（追加时间戳）再判 `provider is None`；dev/staging 无 key 时，连续 >10 次/分会先返回 429 而非 503。属可辩护的突发保护行为，非缺陷。
- **O-3 配额 check-then-act 存在轻微 TOCTOU。** 同一 guest 并发请求可能都通过 `_check_daily_quota` 后再各自 `_record_usage`，使日配额被小幅突破。软性产品配额 + 单进程内存限流为主防线，影响可忽略。
- **O-4 httpx.AsyncClient 每次调用新建。** `provider.py:86` 每请求 `async with httpx.AsyncClient(...)`，无跨请求连接复用。AI 调用低频长耗时，开销可忽略。

---

## 五、结论

**实现层面：有条件通过。** T-M4-01a/01b/01c 的验收点均有对应实现与测试，本地实测全绿（AI 相关 139 项、全套 364 passed），脱敏日志、降级、限流配额、用量审计、production 拒绝启动、提案校验平价等关键安全/正确性属性都有断言覆盖；01d 端点/鉴权/ADR-003 已落地，仅余真实密钥冒烟（D-EXEC-04，外部）与产物密钥扫描（T-M5-02c）两项如实记录的阻塞。代码质量高，未发现导致错误写库或密钥/敏感原文泄露的硬缺陷。

**交付层面：不通过。** 存在 1 项 P1（R-M4-01 实现全量未提交、完成门禁未达成）+ 3 项 P2（R-M4-02 收口报告测试事实失真、R-M4-03 interpret 契约与权威文档不一致、R-M4-04 新增 server CI job 的 ruff 依赖缺口会使门禁失败）+ 3 项 P3（R-M4-05/06/07）。closure 报告"~95% 完成"对**实现**基本属实，但对**交付治理**乐观失真：在其声称的"完成"状态下，代码尚未进入版本历史、远端 CI 从未覆盖服务端且首次接入即有依赖缺口、报告本身的测试数字不可信。

**优先级建议：**
1. **必做（判定 T-M4-01 合规完成前）：** R-M4-01（按任务提交+推送+确认 CI）、R-M4-04（补 ruff 依赖使 server 门禁可跑）。
2. **应做：** R-M4-02（订正收口报告测试事实）、R-M4-03（先改文档对齐 interpret 契约）。
3. **可挂账：** R-M4-05/06/07 与 O-1~O-4。

**门禁汇总：** 本地 pytest ✅（364/1/1，error 为环境）；本地 ruff ❌（未安装，且依赖未声明，R-M4-04）；远端 CI ⚠️ 未能确认（实现未提交，且 server job 从未在远端运行过）。
