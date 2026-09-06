# T-M4-01 审查报告（第 3 轮：漏洞深挖 + 交付治理复核）

- **审查日期：** 2026-09-06
- **审查模式：** Review（只读；不修改项目代码/测试/配置）
- **审查对象：** T-M4-01 模块（AI Gateway 基建）现有实现深挖
- **基线：** HEAD `3b73dd5` + 工作树 30 项未提交改动（含另一并行会话正在进行的 R-M4-02/05/06 修复草稿——本文在对应发现中标注"工作树已改"）
- **依据：** `docs/milestones/production-execution-plan.md`、`docs/architecture/ai-architecture.md`、`APP-CONTEXT §6.4/§10.3`、`docs/engineering/通用任务执行规范.md`、上轮 `docs/review/T-M4-01-review.md`（第 2 轮 R-M4-01..07）
- **范围：** `server/src/modules/ai/` 全部文件、`server/src/common/{config,main,logging,request_id}.py`、`server/src/models/ai.py`、`server/Dockerfile`、`docker-compose.yml`、`.github/workflows/ci.yml`、`server/pyproject.toml`、远端 GitHub CI run 日志
- **排除：** 客户端 `app/`、真实 DashScope 冒烟、T-M4-02/03 业务逻辑细节（仅在其与 01 共用文件时附带核对）

> **审查原则：** 本审查只读 + 用可验证命令取证；不写项目代码；对已在工作树进行中的修复，在对应条目注明"工作树已改"但不视为已闭环（未提交、未过 CI）。

---

## 一、验证命令与实测结果

| 命令 | 结果 | 备注 |
|---|---|---|
| `git log --oneline -1` | `3b73dd5 fix(ci): 补全 AndroidManifest uses-feature + bridge 命令计数更新` | HEAD 已推进到 3b73dd5（较第 2 轮的 `f71e771` 前进多次） |
| `git status --short`（基线钉住时） | 空 | 工作树干净；其后另一并行会话产生 30 项未提交改动 |
| `gh run list --limit 5` | **5 个 server job 全失败**（最近 `34037172838`） | `34035608951` 对应提交 `435342b`（"CI 验证通过"）——**提交信息与远端 CI 实测矛盾** |
| `gh run view --job 101497253099 --log` 中 pytest 段 | `ImportError while loading conftest … starlette.exceptions.StarletteDeprecationWarning … Using httpx with starlette.testclient is deprecated; install httpx2 instead`；**exit 4，0 条测试被收集** | 详见 R-M4-38 |
| `pytest tests/ -q --tb=no`（本机） | **404 passed, 1 skipped, 1 error** | error 为 Windows 环境下 `test_stats_exports.py` 的 `PermissionError`（与 AI 无关）；与第 2 轮的 364 差距反映工作树新增测试 |
| AI 相关 6 文件收集 | **142 项**（config 14 / auth 40 / main 6 / ai_gateway 49 / parity 17 / validation 16） | 与第 2 轮的 139 差距 = +3（T-M4-02 提交 +2、R-M4-06 修复草稿 +1） |
| `python -c "from src.modules.ai.service import AIService; AIService._parse_json_output(None)"` | `AttributeError: 'NoneType' object has no attribute 'strip'` | 见 R-M4-40 |
| `python -c` TestClient 测试 Unicode X-Request-Id | httpx 客户端侧先报 `UnicodeEncodeError`；服务端未触发 | 实际走线（latin-1 字节）不崩溃；降为观察项 O-5 |
| 本地 `python -m ruff --version` | `ruff 0.16.6` | 本机可用；CI 也可用（runner 预装）；但 `pyproject.toml` 的 dev 组仍未声明 ruff 依赖（R-M4-04 的可复现性缺口仍在） |

**只读边界确认：** 本次审查未修改任何项目代码、测试、配置、依赖或迁移；仅新建本审查文档。

---

## 二、第 2 轮发现（R-M4-01..07）复核

| # | 标题 | 第 2 轮状态 | 本轮复核 | 备注 |
|---|---|---|---|---|
| R-M4-01 | 实现全量未提交，完成门禁未达成 | 未修复 | 🔨 **工作树已改**：M4 已提交（`c101afb` 起），但**远端 CI 仍未过**（详见 R-M4-38） | 提交门禁达成 ≠ CI 门禁达成 |
| R-M4-02 | `T-M4-01-closure.md` 测试事实失真 | 未修复 | 🔨 **工作树已改**（未提交）：草稿已按实测订正（142 项 / 完整 404/1/1 / 删除"未处理项 #3"），提交信息已对齐 364 为"首轮回审基线"、当前工作树为 404 | 等待提交 |
| R-M4-03 | `/ai/interpret` 返回未校验最小解析 vs 文档"AI 只返回 ActionProposal" | 未修复 | ❌ **未修复**：`docs/milestones/production-execution-plan.md:599` 仍写 "`POST /ai/interpret`（文本 → ActionProposal）"；`docs/architecture/ai-architecture.md:5` 仍写"AI 只返回通过 JSON Schema 校验的 ActionProposal"；无 interpret 中间表示的说明 | 文档/实现契约漂移 |
| R-M4-04 | 新增 server CI job 的 `ruff check` 缺 ruff 依赖声明 | 未修复 | ⚠️ **部分**：`pyproject.toml` 已加 `[tool.ruff]` 配置段，但 dev 组仍未声明 ruff 依赖（`grep -n ruff pyproject.toml` 只命中 `tool.ruff`，无 `[project.optional-dependencies].dev` 行） | CI 能过是因为 ubuntu-latest runner 碰巧预装了 ruff——可复现性缺口未补 |
| R-M4-05 | `validator.py` docstring 引用不存在的 `assembly.py` | 未修复 | 🔨 **工作树已改**（未提交）：docstring 改为"Assembly happens on the client (see ADR-03)；DB context applied server-side via `context.py`" | 等待提交 |
| R-M4-06 | 重试路径首次成功的 tokens 未计入 AIUsage | 未修复 | 🔨 **工作树已改**（未提交）：`interpret` 路径新增 `retry_prompt_tokens`/`retry_completion_tokens` 累加；`_record_usage` 新增 `extra_*_tokens` 参数 | 但 **`_generate_narrative` 路径未同步修**（同一 MAX_ATTEMPTS 循环，attempt-1 tokens 仍丢失），详见 R-M4-43 |
| R-M4-07 | 上一轮评审报告未落盘 | 未修复 | ✅ **已修复**：`docs/review/T-M4-01-review.md`（`3e511ed`）已归档并使用 `R-M4-nn` 编号 | — |

---

## 三、新发现问题

### 🔴 R-M4-38（P1 · CI/依赖）— 远端 CI server job 完全失效；提交信息"CI 验证通过"与实测矛盾

- **状态：** 未修复
- **位置：** `.github/workflows/ci.yml` server job；`server/pyproject.toml` `filterwarnings=["error", ...]`；`server/Dockerfile` Python 3.12 / CI Python 3.13 / `requires-python>=3.12` 浮点绑定
- **问题与触发条件：** `fastapi>=0.115,<1` 浮点解析到 fastapi 0.141.1 时配套解析到 starlette ≥1.6.0；starlette 1.6.0 在 `testclient.py:47` 发出 `StarletteDeprecationWarning`（"Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead"）；pyproject 把 warning 升格为 error；该 warning 在 `conftest.py` 导入期触发 → **pytest ImportError，exit 4，0 条测试被收集**。CI 上 5 个最近的 server run 全部失败；最新一次 `34037172838`（`7228493`）的 log 直接给出：`ImportError while loading conftest '/home/runner/work/Aozora/Aozora/server/tests/conftest.py'` 后 `exit 4`。本机 starlette 1.6.0 + httpx 0.28.1 → 404 passed（本机 `filterwarnings` 行为与 CI 不同，因为 conftest 导入期 warning 是否被 pytest 捕获取决于 pytest 启动时机；CI 上 exit 4 是确定性结果）。
- **证据或复现命令：**
  - `gh run view --job 101497253099 --log 2>&1 | grep -A2 "ImportError\|exit code"` 显示 conftest ImportError + exit 4
  - `python -c "import starlette; print(starlette.__version__)"` → 1.6.0
  - `grep -n filterwarnings server/pyproject.toml` → `["error", ...]`
- **影响：** ① T-M4-01（及所有 server 任务）的远端测试门禁名存实亡——CI 显示"失败"但无人响应；② 提交 `435342b` 消息"CI 验证通过"与远端实测矛盾，提交记录不可信；③ 任何 server 侧的回归（含 T-M4-02/03 业务代码）都未被 CI 守护过；④ 本机/CI 行为分裂（本机 pass、CI exit 4）掩盖了问题。
- **修复建议：** 三选一并补另一项：① 把 fastapi/starlette 钉死到与本机一致的版本（如 `fastapi==0.141.1` 或用 `uv.lock`/pip-tools 锁定），确保 CI 与本机同一依赖图；② 在 `filterwarnings` 加 `ignore::starlette.exceptions.StarletteDeprecationWarning` 短期止血；③ 长期：迁移到 httpx2 或锁定 starlette 版本；同时补一份 lockfile（`uv.lock` / `requirements.lock`）消除浮点漂移。修复后重跑 CI，确认 `pytest` 步骤绿。
- **复验方法：** 远端 server job `pytest` 步骤绿；`gh run list` 出现一次 server job 的 completed/success。

### 🟡 R-M4-39（P2 · 部署）— 生产镜像未 COPY `contracts/`，validator 首次调用 FileNotFoundError；docker-compose build 上下文错误

- **状态：** 未修复
- **位置：** `server/Dockerfile` 第 9、12、30 行；`docker-compose.yml` 第 22 行；`server/src/modules/ai/validator.py:31` `_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "contracts" / "action-proposal" / "schema.json"`
- **问题与触发条件：** Dockerfile 只 COPY `pyproject.toml` 与 `src/`，未包含 `contracts/`。容器内 `/app/src/modules/ai/validator.py` 的 `parents[4]` 解析为 `/`（仓库根在容器里不存在），最终路径 `/contracts/action-proposal/schema.json`——不存在。`validator.py` 在模块层 `open(_SCHEMA_PATH)` 读 schema，被 `service.py:438` 的 `from .validator import validate_proposal` 懒加载触发——即**第一次 `/ai/validate` 或 `/ai/execute` 请求**抛出 `FileNotFoundError`，被 FastAPI 转成 500（无降级日志、无安全事件记录、无客户端友好错误码）。`_load_schema()` 在模块层立即执行——任何后续 import 也会失败，但懒加载意味着首次命中才爆炸。
- **额外：** `docker-compose.yml` 第 22 行 `build: .` 指向仓库根上下文；仓库根**无 Dockerfile**（`ls Dockerfile` 不存在），`docker compose up --build` 在构建第一步即失败（"failed to read dockerfile: open Dockerfile"），整条 compose 链路不可用。
- **证据或复现命令：**
  - `cat server/Dockerfile | grep -n "COPY\|FROM"` → 仅 `COPY pyproject.toml`、`COPY src/`，无 contracts
  - `ls Dockerfile` → `No such file or directory`（仓库根）
  - `python -c "from pathlib import Path; p = Path('/app/src/modules/ai/validator.py').resolve(); print(p.parents[4])"` → `/`
- **影响：** ① 容器部署后 `/ai/validate` / `/ai/execute` 必 500，T-M4-02 执行链完全不可用；② 任何 compose 启动尝试直接失败；③ 该问题未被 CI 覆盖（CI 走源码目录，contracts 在仓库根可用，本地 `pytest` 通过）。
- **修复建议：** ① Dockerfile 增加 `COPY ../contracts ./contracts/`（构建上下文调整）或用 `--mount`/multistage 把 contracts 从仓库根拷入镜像；② 把 schema 路径改为相对于包自身（如 `server/src/modules/ai/contracts/action-proposal/schema.json`，打包时随 wheel 分发）或内联 schema 字符串；③ docker-compose 改为 `build: { context: ., dockerfile: server/Dockerfile }`，并同步把 contracts 拷入构建上下文；④ 加一条启动期校验：`validate_proposal({})` 在 lifespan 内跑一次，把部署期崩溃前移到启动期（fail-fast 而非首次请求期）。
- **复验方法：** 容器内 `ls /app/contracts/action-proposal/schema.json` 存在；容器内 `/ai/validate` 返回 200（合法提案）或 422（非法提案）而非 500；`docker compose up --build` 构建成功。

### 🟢 R-M4-40（P3 · 鲁棒性）— Provider 200 响应 `content: null` 触发 AttributeError → 500，违反 §6.4 降级契约

- **状态：** 未修复
- **位置：** `server/src/modules/ai/provider.py:130` `content = body["choices"][0]["message"]["content"]`；`server/src/modules/ai/service.py:263` `_parse_json_output(content)` 第 276 行 `text = content.strip()`；`service.py:196-207` 重试循环只 `except ValueError`
- **问题与触发条件：** OpenAI 兼容 API 在某些边界情况下（content filter 触发、refusal 字段、部分 provider 在 `finish_reason=length` 时）会在 200 响应里返回 `"content": null`。`provider.py:130` 的 try/except 捕获 `ValueError/KeyError/IndexError/TypeError`，但 `None` 不是以上任意一种——`ProviderResult(content=None, ...)` 成功构造。下游 `_parse_json_output(None)` 调用 `None.strip()` → `AttributeError`。`interpret` 的重试循环只捕获 `ValueError`，`AttributeError` 穿透到 FastAPI → **500**，违反 §6.4 的降级契约（应 503 `ai_unavailable` 让客户端回退本地 NLU）。
- **证据或复现命令：** `python -c "from src.modules.ai.service import AIService; AIService._parse_json_output(None)"` → `AttributeError: 'NoneType' object has no attribute 'strip'`。同类问题：`usage` 字段 null 时 `int(None)` → TypeError → `bad_response` 重试（无意义重试一次、双计费）。
- **影响：** 罕见但确定性的 500；跳过失败日志的 `outcome` 枚举路径（`_log_failure` 不被调用）；跳过 AIUsage 记录；客户端看到非契约错误码。
- **修复建议：** `provider.py:128-136` 的 try 块里加 `if not isinstance(content, str): raise ProviderError(status=200, retryable=True, reason="bad_response")`；或在 `_parse_json_output` 入口加 `if not isinstance(content, str): raise ValueError(...)` 把非 str 归一到 ValueError 让重试/降级路径吃掉。
- **复验方法：** 用 MockTransport 构造 200 响应 `{"choices":[{"message":{"content":null}}],"usage":{}}`，断言 `/ai/interpret` 返回 503 `ai_unavailable` 而非 500；对应 WARNING 日志出现 `outcome: bad_response`。

### 🟢 R-M4-41（P3 · 正确性/成本，03b  incidental）— 叙述缓存 key 在 `/ai/report` 的 get/set 两端使用不同 book_id 规范化，导致缓存永不命中

- **状态：** 未修复（incidental，主责 03b；本审查仅在共用 `service.py` 中附带发现）
- **位置：** `server/src/modules/ai/service.py:757`（读缓存用 `req.book_id` 原始字符串）；`server/src/modules/ai/service.py:890`（写缓存用 `fact_pack.book_id`）；`server/src/modules/ai/fact_pack.py:252` `book_id=str(book_id)`（uuid 字符串化后小写）
- **问题与触发条件：** `AIReportRequest.book_id: str` 无 pattern 约束，`uuid.UUID(req.book_id)` 接受大写/带大括号/urn 形式等所有合法 UUID 字符串。`str(uuid.UUID(...))` 总是小写无大括号。若客户端发送 `"550E8400-E29B-41D4-A716-446655440000"`（大写），则 `req.book_id`（原始大写）与 `fact_pack.book_id`（规范化小写）不一致 → 缓存 get/set 键不同 → 缓存永不命中 → 相同数据的月度报告重复调 LLM、重复计费。R-M4-09 修复（叙述缓存）的"同数据不重复计费"验收在此输入下失效。
- **证据或复现命令：** 代码对比；`AIReportRequest(book_id="550E8400-E29B-41D4-A716-446655440000", period="2026-09").book_id` 保持大写；`str(uuid.UUID(...))` 小写。
- **影响：** 客户端偶发输入可绕过缓存 → 额外 LLM 成本（叙述生成约 1k tokens/次）；成本看板偏低。客户端当前实现可能已规范化（无复现用例），但契约层未强制。
- **修复建议：** 在 `generate_report` 入口先 `uuid.UUID(req.book_id)` 再 `str(...)` 规范化，读缓存与写缓存都用规范化后的字符串；或在 `AIReportRequest` 的 `book_id` 加 `pattern` 约束强制小写。
- **复验方法：** 测试：同一 book 用大写/小写两种 `book_id` 连续调 `/ai/report` 两次，断言 LLM 仅被调用一次（第二次 `narrative_cached=true`）。

### 🟢 R-M4-42（P3 · 配置）— `ai_timeout_seconds` 无上界校验，设大即破 25s 客户端取消预算

- **状态：** 未修复
- **位置：** `server/src/common/config.py:131-135` `ai_timeout_seconds: float = Field(default=10.0, gt=0, ...)`；`server/src/modules/ai/service.py:75` `MAX_ATTEMPTS = 2` 注释"2 × timeout + overhead < 25s"
- **问题与触发条件：** 验收契约要求"2 × timeout + overhead < 25s"（`MAX_ATTEMPTS` 注释、`config.py` 字段注释都明文），但字段只有 `gt=0`，无 `le=`；运维设 `AI_TIMEOUT_SECONDS=15` → 2×15=30s > 25s，客户端已在 25s 取消，服务端仍在等待/计费；无启动告警、无拒绝。
- **证据或复现命令：** `grep -n "gt=0" server/src/common/config.py` 第 133 行；`grep -n "25" server/src/modules/ai/service.py` 第 80 行"25 s client-cancel budget"。
- **影响：** 配置漂移下客户端与服务端契约错位，降级时序破坏。
- **修复建议：** 字段加 `le=12`（留 1s overhead），或 `_check_production_safety` 中加 `if self.ai_timeout_seconds * 2 >= 25: raise ValueError(...)`。
- **复验方法：** `AI_TIMEOUT_SECONDS=15 python -c "from src.common.config import Settings; Settings(env='production', ...)"` → 应拒绝。

### 🟢 R-M4-43（P3 · 审计/成本，carry-over from R-M4-06）— `_generate_narrative` 同族问题：重试 tokens 未累计

- **状态：** 未修复
- **位置：** `server/src/modules/ai/service.py:830-859`（`_generate_narrative` 的 MAX_ATTEMPTS 循环 + 单次 `_record_usage`）
- **问题与触发条件：** 与 R-M4-06 同构——attempt 1 合法响应但非法 JSON → attempt 2 成功，attempt 1 tokens 不入 `AIUsage`。R-M4-06 的工作树修复（`retry_prompt_tokens` 累加 + `extra_*_tokens` 参数）只覆盖了 `interpret` 路径，`_generate_narrative` 路径未同步改——`_record_usage` 调用仍在循环外、只传 `result` 不传累加值。
- **证据或复现命令：** `git diff server/src/modules/ai/service.py` 工作树改动只命中 `interpret` 路径与 `_record_usage` 签名；`_generate_narrative` 无对应 diff 段。
- **影响：** 叙述生成场景下 retry tokens 不计费、不入审计；成本看板偏低。
- **修复建议：** 在 `_generate_narrative` 的循环里同步累加 `retry_*_tokens`，并在 `_record_usage(...)` 调用时作为 `extra_*_tokens` 传入；或在循环内部每次拿到 `result` 就立即累加到局部变量、循环结束后一次性记录。
- **复验方法：** Mock provider 在 attempt 1 返回合法 ProviderResult 但 content 为非法 JSON、attempt 2 返回合法 JSON，断言 AIUsage.tokens == 两次 prompt+completion 之和。

---

## 四、观察项（非缺陷，记录备查）

- **O-5（X-Request-Id 无约束/无截断）** `src/common/request_id.py` 接受任意客户端字符串，原样回传到 response header、原样进入 JSON log（`logging.py` 的 `JSONFormatter` 不截断）、AIUsage 才截到 64。实际走线（latin-1 字节）不会崩溃服务端（非 latin-1 字符在传输时被 latin-1 解码为乱码，回显也为乱码），但 h11 上限 ~16KB 意味着单条日志可被撑到 16KB；日志存储/查询/告警成本轻微上升。**建议：** middleware 中加长度上限（如 128）+ 字符集校验（`^[A-Za-z0-9\-_]+$`），非法值落默认 UUID。

- **O-6（tzdata 依赖未显式声明）** `zoneinfo` 需要系统 tzdata 或 pip 的 `tzdata` 包。`python:3.12-slim` 的 Debian base 通常包含 tzdata，但若未来换更瘦的 base image（distroless / alpine），`ZoneInfo("Asia/Shanghai")` 会静默失败 → 回落到 UTC（R-M4-41 的 guest 配额日界从本地午夜漂移到 UTC 0 点）。**建议：** 部署前在镜像里跑 `python -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai')"` 作为健康检查；或在 `pyproject.toml` 显式加 `tzdata` 依赖。

- **O-7（Python 版本漂移）** Dockerfile `python:3.12-slim` vs CI `python-version: "3.13"` vs `requires-python: >=3.12`（浮点）。三处不一致易引入微妙行为差异（R-M4-38 的 starlette 警告即是其一）。**建议：** 统一钉死到 3.13（或 3.12），并在 Dockerfile/CI/pyproject 三处同步。

- **O-8（配额计数的时钟漂移）** `AIUsage.created_at` 用 `server_default=func.now()`（DB 时钟），配额 check 用 `day_start = local_day_start_utc(...)`（应用时钟）。DB 与应用时钟漂移会轻微移动配额日界。**影响可忽略**，记录备查。

- **O-9（`AIInterpretRequest.context` 未使用）** `schemas.py:18` 声明 `context: dict[str, Any] | None = None` 但 `interpret` 路径从不读它；任意嵌套 dict 无上限。FastAPI 的 body 解析成本 + 内存占用随恶意输入上升。**建议：** v1 直接移除；若留给 02/03 扩展，加 `max_length` 或 schema 约束。

---

## 五、结论

**实现层：有条件通过。** T-M4-01a/01b/01c 的验收点代码与测试仍在位，本机 pytest 404 passed / 1 skipped / 1 error（error 为 Windows 环境问题），AI 相关 142 项全绿；脱敏日志、降级、限流配额、production 拒绝启动、提案校验平价等关键安全属性仍有断言覆盖。新发现 R-M4-40（`content: null` → 500）是唯一的正确性缺陷，但触发条件罕见（OpenAI 兼容 API 边界情况）；R-M4-41（叙述缓存 key 不一致）是成本正确性缺陷，触发条件更罕见（客户端发大写 UUID）。

**交付层：不通过。** 比第 2 轮更严重——**远端 CI server job 实际从未跑过任何一条测试**（R-M4-38：starlette 警告 + filterwarnings=error → conftest 导入崩溃 → pytest exit 4），且最近一次声称"CI 验证通过"的提交（`435342b`）与远端实测矛盾。生产镜像（Dockerfile + compose）无法构建与运行（R-M4-39：contracts/ 未拷贝、compose 构建上下文错误），T-M4-02 执行链在容器部署时首次调用必 500。

**工作树状态：** 30 项未提交改动，含 R-M4-02/05/06 的修复草稿（另一并行会话正在进行）。这些修复**尚未提交、未过 CI、未落盘**，不可视为闭环；R-M4-03/04 仍完全未修；R-M4-06 的工作树修复未覆盖 `_generate_narrative` 同族问题（R-M4-43）。

**优先级建议：**

1. **必做（判定 T-M4-01 合规完成前）：**
   - R-M4-38（钉死 fastapi/starlette 版本或补 lockfile；CI server job 必须真绿一次）
   - R-M4-04 收尾（dev 组声明 ruff 依赖，消除"runner 碰巧预装"的可复现性缺口）
   - R-M4-39（Dockerfile COPY contracts；compose 构建上下文修正）

2. **应做（提交前）：**
   - R-M4-02 / R-M4-05 / R-M4-06（工作树草稿提交 + 远端 CI 通过）
   - R-M4-03（文档对齐 interpret 中间表示）

3. **可挂账：**
   - R-M4-40（content null 鲁棒性）
   - R-M4-41（叙述缓存 key 规范化；03b 主责）
   - R-M4-42（timeout 上界）
   - R-M4-43（`_generate_narrative` 同族 tokens 累计）
   - O-5..O-9

**门禁汇总：**

| 项 | 状态 |
|---|---|
| 本机 pytest（404/1/1） | ✅ 通过（error 为环境） |
| 本机 ruff（0.16.6） | ✅ 通过 |
| 远端 CI server job | ❌ **5 次连续失败**（R-M4-38：exit 4） |
| 远端 CI Client/Android job | ❌ 失败（本审查不展开） |
| 容器部署（compose + server Dockerfile） | ❌ 构建失败 + validator 首次调用 500（R-M4-39） |
| 真实 DashScope 冒烟 | 🔨 未执行（归属 D-EXEC-04） |
