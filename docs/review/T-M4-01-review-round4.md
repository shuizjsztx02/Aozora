# T-M4-01 审查报告（第 4 轮：全量重审）

- **审查日期：** 2026-09-06
- **审查模式：** Review（只读；不修改项目代码/测试/配置）
- **审查对象：** T-M4-01 模块（AI Gateway 基建）当前实现状态，含工作树未提交改动
- **基线：** HEAD `05f69ed`（"fix(test): a11y.test.ts typecheck 修复"）+ 工作树 15 项已修改 + 1 项未跟踪
- **依据：** `docs/milestones/production-execution-plan.md`、`docs/architecture/ai-architecture.md`、`APP-CONTEXT §6.4/§10.3`、前三轮审查报告（`docs/review/T-M4-01-review.md`、`T-M4-01-review-round2.md`、`T-M4-01-review-2.md`）、`T-M4-01-closure.md`
- **范围：** `server/src/modules/ai/` 全目录（含 T-M4-04e 未提交改动）、`server/src/common/config.py`（AI 段）、`server/src/main.py`（AI 启动/关闭段）、`server/src/models/ai.py`、AI 相关测试、`server/Dockerfile`、`docker-compose.yml`、`server/pyproject.toml`、`.github/workflows/ci.yml`
- **排除：** 客户端 `app/`、真实 DashScope 冒烟、T-M4-02/03 业务逻辑细节

---

## 一、验证命令与实测结果

| 命令 | 结果 | 备注 |
|---|---|---|
| `git log --oneline -1` | `05f69ed` | HEAD 已推进到第 3 轮后的两次新提交（`f93c22f` T-M4-03c、`05f69ed` a11y 修复） |
| `git status --short` | 15 modified + 2 untracked | 工作树含 T-M4-04d/T-M4-05 未提交改动（含 AI 模块 6 个文件） |
| `pytest tests/test_ai_gateway.py test_ai_proposal_parity.py test_ai_proposal_validation.py test_config.py test_auth.py test_main.py -q`（工作树） | **142 passed** | T-M4-01 六文件全绿，与第 2 轮一致 |
| `pytest tests/ -q --tb=no`（工作树） | **415 passed, 7 failed, 1 skipped, 1 error** | 7 failed 均为 `test_image_parse.py`（T-M4-04e 未跟踪测试）；error 为 Windows 环境 exports 项 |
| `pytest tests/ -q --tb=no`（纯净 HEAD stash） | **407 passed, 15 failed, 1 skipped, 1 error** | 15 failed 含 `test_image_parse.py` 8 项（测试文件未跟踪故 stash 不影响）+ `test_main.py::test_redoc_available` 1 项 error |
| `python -m ruff check src/ tests/`（工作树） | **9 errors** | 含 `F821 Undefined name AIParseImageResponse`、`F401` 未使用导入、`I001` 排序问题，详见 N-1 |
| `gh run list --limit 5` | 最近 3 个 run **全失败** | 最新成功 run `34040351268`（`a6f5c88`）；最新 run `34042066911` server pytest error（`test_main.py::test_redoc_available`），Client test 失败（`a11y.test.ts`） |

**只读边界确认：** 本次审查未修改任何项目代码、测试、配置、依赖或迁移；仅运行只读检查与测试，并新建本审查文档。

---

## 二、前三轮发现（R-M4-01~07, R-M4-38~43）复核

| # | 标题 | 级别 | 本轮复核 | 证据 |
|---|---|---|---|---|
| R-M4-01 | 实现全量未提交 | P1 | ✅ **已关闭** | AI 模块已随 `c101afb`/`435342b`/`3e1607b` 入库；`git ls-files` 确认无 untracked |
| R-M4-02 | closure 报告测试事实失真 | P2 | ✅ **已关闭** | `T-M4-01-closure.md` 已按实测订正（142 项 / 364/1/1） |
| R-M4-03 | interpret 契约与文档脱节 | P2 | ✅ **已关闭** | `ai-architecture.md` 新增流水线节；执行计划 01d 行已改 |
| R-M4-04 | ruff 依赖未声明 | P2 | ✅ **已关闭** | `pyproject.toml` dev 组已声明 `ruff>=0.8,<1`；`[tool.ruff]` 配置齐全 |
| R-M4-05 | validator.py docstring 引用不存在的 assembly.py | P3 | ✅ **已关闭** | docstring 已改为引用 ADR-003 / `context.py` |
| R-M4-06 | 重试路径 tokens 未计入 AIUsage | P3 | ✅ **已关闭** | `interpret` 循环已有 `retry_*_tokens` 累加 + `extra_*_tokens` 参数 |
| R-M4-07 | 上一轮评审报告未落盘 | P3 | ✅ **已关闭** | `docs/review/T-M4-01-review.md` 已归档 |
| R-M4-38 | CI server job 完全失效（exit 4） | P1 | ✅ **已关闭** | `filterwarnings` 已补 starlette 警告过滤；run `34040351268` server job 绿 |
| R-M4-39 | Dockerfile 未 COPY contracts/ + compose 构建上下文错误 | P2 | ✅ **已关闭** | Dockerfile 增加 `COPY contracts/ /contracts/`；compose 改为 `context: . + dockerfile: server/Dockerfile` |
| R-M4-40 | `content: null` → AttributeError → 500 | P3 | ✅ **已关闭** | `provider.py:147-148` 新增 `if not isinstance(content, str): raise ProviderError(...)` |
| R-M4-41 | 叙述缓存 key book_id 大小写不一致 | P3 | ✅ **已关闭** | `generate_report` 入口 `uuid.UUID(req.book_id)` → `str(book_id)` 规范化 |
| R-M4-42 | `ai_timeout_seconds` 无上界 | P3 | ✅ **已关闭** | `config.py:137` 已加 `le=12.0` |
| R-M4-43 | `_generate_narrative` 同族 tokens 未累计 | P3 | ✅ **已关闭** | `service.py:857-908` 已加 `retry_*_tokens` 累加 + 失败路径 `_record_usage` |

**前三轮 13 项发现全部关闭。** 本轮审查基于当前代码状态重新独立验证，不依赖历史结论。

---

## 三、当前 T-M4-01 代码独立验证

### 3.1 验收标准逐项对照（HEAD `05f69ed` 已提交代码）

#### T-M4-01a Provider 适配与模块骨架 — ✅ 达成

| 验收点 | 代码位置 | 验证结果 |
|---|---|---|
| provider adapter 接口 + DashScope（OpenAI 兼容，httpx async） | `provider.py:75-157` | ✅ 连接池复用（`_client` 单例），`aclose()` 关闭 |
| Settings 扩展（模型/超时/重试，key 仅环境变量） | `config.py:110-174` | ✅ 全部 `AliasChoices` + 默认值 |
| production 缺 key 拒绝启动 | `config.py:203-207` | ✅ `_check_production_safety` 校验 |
| dev/staging 缺 key 503 降级 | `main.py:73-77`、`service.py:165-169` | ✅ 启动警告 + 运行时 503 |
| mock provider | `provider.py:84` transport 注入点 | ✅ 测试用 MockTransport |
| `ai_timeout_seconds` 上界 | `config.py:137` `le=12.0` | ✅ 2×12+1=25s 契约保持 |

#### T-M4-01b Prompt registry 与服务端提案校验 — ✅ 达成

| 验收点 | 代码位置 | 验证结果 |
|---|---|---|
| Prompt registry（版本化、按任务） | `prompts.py:19-143` | ✅ `PromptTemplate` dataclass + `REGISTRY` |
| LLM 输出 JSON 解析（容忍 markdown fence） | `service.py:273-316` | ✅ 快速路径 + fence 回退 |
| Python ActionProposal 校验器（Draft-07 + format: date） | `validator.py:37-73` | ✅ 完整日期往返 + FormatChecker |
| 跨语言平价测试 | `test_ai_proposal_parity.py`（17 项） | ✅ 含漂移守卫 |
| 非法 JSON 重试一次、仍失败降级 | `service.py:182-214` | ✅ MAX_ATTEMPTS=2 + ValueError 重试 |

#### T-M4-01c 限流、用量审计与降级 — ✅ 达成

| 验收点 | 代码位置 | 验证结果 |
|---|---|---|
| 用户级限流 | `service.py:238-254` | ✅ 内存桶 + 60s 窗口 + 清理 |
| 配额（guest 日配额） | `service.py:256-269` | ✅ 按用户时区日起算 |
| AIUsage append-only 审计 | `service.py:320-354` | ✅ 含重试 tokens 累计 |
| 超时语义（12s 慢速 / 25s 取消） | `service.py:81`、`:408-434` | ✅ SLOW_HINT_SECONDS + `_log_slow_if_needed` |
| provider 故障 → 降级响应 | `service.py:190-196` | ✅ 503 `ai_unavailable` |
| 脱敏日志 | `service.py:356-402` | ✅ 仅元数据，不记录输入/输出 |
| `content: null` 鲁棒性 | `provider.py:147-148` | ✅ 非 str → ProviderError bad_response |

#### T-M4-01d 网关 API 与端到端收口 — ✅ 达成

| 验收点 | 代码位置 | 验证结果 |
|---|---|---|
| `POST /ai/interpret` + Bearer 鉴权 | `routes.py:35-50` | ✅ `Depends(get_current_user)` |
| ADR-003 WebView AI 通道 | `docs/adr/ADR-003-webview-ai-channel.md` | ✅ 已归档 |
| 端到端测试 | `test_ai_gateway.py`（49 项） | ✅ 全绿 |
| Dockerfile contracts/ 拷贝 | `Dockerfile:37` | ✅ `COPY contracts/ /contracts/` |
| docker-compose 构建上下文 | `docker-compose.yml:24-26` | ✅ `context: . + dockerfile: server/Dockerfile` |

### 3.2 T-M4-01 专属测试（142 项）实测

```
tests/test_config.py                14 passed
tests/test_auth.py                  40 passed
tests/test_main.py                   6 passed
tests/test_ai_gateway.py            49 passed
tests/test_ai_proposal_parity.py    17 passed
tests/test_ai_proposal_validation.py 16 passed
                              Total: 142 passed ✅
```

### 3.3 远端 CI 状态

| 项 | 状态 | 说明 |
|---|---|---|
| 最近一次成功 run | ✅ `34040351268`（`a6f5c88`） | Client/Android/Server 三 job 全绿 |
| 最新 run | ❌ `34042066911`（`05f69ed`） | Client: a11y.test.ts 1 failed；Server: test_main.py 1 error（attachments 表不存在） |
| Server CI ruff 步骤 | ✅ 绿（`34042066911`） | ruff 在 CI 上通过 |
| Server CI pytest 步骤 | ⚠️ error | `test_main.py::TestOpenAPI::test_redoc_available` — lifespan 中 attachment cleanup 触发 `OperationalError: no such table: attachments` |

**CI 回归分析：** Server pytest 从 `407 passed`（run `34040351268`）退化为 `407 passed, 1 error`（run `34042066911`）。error 来自 `main.py:55-67` 的 attachment cleanup 在 conftest 的 SQLite 测试 DB 上找不到 `attachments` 表。**该 error 非 T-M4-01 代码引入**——它由 T-M4-04c 的 lifespan attachment cleanup 代码（`main.py:55-67`）与 conftest 测试 DB schema 不完整之间的不匹配导致。但此 error 使 `pytest` 步骤 exit 1，**阻塞了全部 server 门禁**（包括 T-M4-01 的测试通过）。

---

## 四、新发现问题

### 🟡 N-1（P2 · CI/工程）— 工作树 T-M4-04e 改动引入 ruff 违规，提交后将破坏 server CI lint 门禁

- **状态：** 未修复（工作树未提交）
- **文件与位置：** `server/src/modules/ai/service.py:950/961-966`、`server/tests/test_image_parse.py:19-47/255`
- **问题与触发条件：** 工作树新增的 `parse_image` 方法有 3 个 ruff 错误（`F821 Undefined name AIParseImageResponse`——字符串注解在 ruff 静态分析下不可解、`I001` import 块排序、`F401` 未使用导入 `Attachment`）；`test_image_parse.py` 有 6 个 ruff 错误（`I001` 排序、`F401` 未使用导入 ×4、`F821` 未定义名 `httpx`）。当前 CI 最新 run `34042066911` 的 ruff 步骤之所以通过，是因为 CI 跑的是 HEAD `05f69ed`（无这些改动）；一旦提交，`ruff check src/ tests/` 必红。
- **证据或复现命令：** `python -m ruff check src/ tests/` → 9 errors（7 fixable）
- **影响：** 提交 T-M4-04e 改动后 server CI lint 门禁失败，阻塞全部 server 测试运行
- **修复建议：** `ruff check --fix` 自动修复排序 + 删除未使用导入；`F821` 的 `AIParseImageResponse` 在 service.py 中已有 `from __future__ import annotations`，但字符串引号使 ruff 无法解析——改为顶层 import 或在方法内直接 import 即可；`test_image_parse.py:255` 的 `httpx.Response` 需加 `import httpx`
- **归属：** T-M4-04d/e，非 T-M4-01 自身；但因改动落在 T-M4-01 的 `service.py`/`provider.py`/`routes.py` 等共用文件，**必须在 T-M4-04 提交前修复**

### 🟡 N-2（P2 · CI/可靠性）— 最新 CI run server pytest error 阻塞门禁，非 T-M4-01 回归但需响应

- **状态：** 未修复
- **文件与位置：** `server/src/main.py:55-67`（lifespan attachment cleanup）；`server/tests/conftest.py`（测试 DB schema 不含 attachments 表）
- **问题与触发条件：** `main.py:55-67` 在 lifespan startup 阶段执行 `cleanup_expired_files(att_session)`，查询 `attachments` 表。CI 的 pytest 使用 conftest 创建的 SQLite 测试 DB，部分测试 fixture 的 DB schema 不含 `attachments` 表（因为 `test_main.py` 的 TestClient 使用完整 app lifespan 但 fixture DB 建表不完整）。异常被 `except Exception: logger.warning(...)` 捕获——**不导致进程崩溃**，但 pytest 的 `filterwarnings = ["error", ...]` 将 `PytestUnhandledThreadExceptionWarning` 升级为 error。
- **证据或复现命令：** `gh run view 34042066911 --log | grep "test_redoc_available"` 确认 error
- **影响：** server CI pytest 步骤 exit 1，**所有 server 测试（含 T-M4-01 的 142 项全绿结果）在 CI 上不被认可为通过**
- **修复建议：** conftest 的测试 DB fixture 增加 `attachments` 表建表（`Base.metadata.create_all` 应包含所有模型），或 lifespan 的 attachment cleanup 在表不存在时优雅降级（catch `OperationalError`  specifically）
- **归属：** T-M4-04c lifespan 改动引入，非 T-M4-01 职责；但直接影响 T-M4-01 的门禁判定

### 🟢 N-3（P3 · 正确性/成本）— `_record_usage` 成本计算整数除法截断

- **状态：** 新发现
- **文件与位置：** `server/src/modules/ai/service.py:341` `cost = total_tokens * price // 1_000_000 if price else 0`
- **问题与触发条件：** `ai_price_per_1m_tokens` 为 `int`，默认 0（未定价）。一旦配置了非零价格（如 `price=500` 即 0.0005 元/千 token），`total_tokens * 500 // 1_000_000` 在 `total_tokens < 2000` 时结果为 0——即一次正常 interpret 调用（~800 tokens）的 cost 记录为 0。
- **证据或复现命令：** `python -c "print(800 * 500 // 1_000_000)"` → 0
- **影响：** 成本看板在单价较低或 tokens 较少时系统性偏低。非安全缺陷；当前 `price=0` 默认值下此路径完全不触发。
- **修复建议：** 改为浮点除法 `cost = int(total_tokens * price / 1_000_000)` 或 `cost = total_tokens * price / 1_000_000`（改为 float 字段）；或改用 `Decimal` 避免精度损失
- **复验方法：** 设置 `ai_price_per_1m_tokens=500`，调用 interpret，断言 `AIUsage.cost > 0`

### 🟢 N-4（P3 · 鲁棒性）— `_parse_json_output` 不处理 fence 同行内容

- **状态：** 新发现
- **文件与位置：** `server/src/modules/ai/service.py:295-305`
- **问题与触发条件：** 当 markdown fence 写成 `` ```json{...}``` ``（JSON 与语言标记同行，无换行），`text.find("\n", fence)` 跳过整个 fence 块，导致 JSON 无法被提取。
- **触发条件极其罕见**：Qwen 模型不会这样输出；仅在手动构造输入或模型异常行为时触发。
- **影响：** 可忽略。现有 `_loads_json_object` 快速路径已覆盖裸 JSON 的绝大多数情况。
- **修复建议：** 不修复，记录备查。若未来出现实际案例，在 fence 分支增加同行解析回退

---

## 五、观察项（非缺陷，记录备查）

- **O-1（平价测试为"共享 fixture + 镜像期望"）** 维持挂账。`test_ai_proposal_parity.py` 不实际运行 ajv/TS；防漂移守卫仅防 fixture 文件增删。可接受为 v1 轻量方案。
- **O-2（限流桶在 no-provider 503 路径仍计入）** 维持挂账。可辩护的突发保护行为。
- **O-3（配额 check-then-act TOCTOU）** 维持挂账。单进程内存限流为主防线，影响可忽略。
- **O-4（httpx.AsyncClient 连接池）** 已修复（`provider.py:92-95` 改为单例 `_client`），`complete_with_images` 复用同一 client。
- **O-5（X-Request-Id 无约束/无截断）** 维持挂账。AIUsage 截到 64 与 column String(64) 对齐。
- **O-6（tzdata 依赖未显式声明）** 维持挂账。`python:3.12-slim` 默认包含。
- **O-7（Python 版本漂移）** 维持挂账。Dockerfile 3.12 / CI 3.13 / pyproject >=3.12。
- **O-8（配额计数的时钟漂移）** 维持挂账。影响可忽略。
- **O-9（`AIInterpretRequest.context` 未使用）** 维持挂账。任意嵌套 dict 无上限，但 FastAPI body 解析受限于请求体大小。

---

## 六、工作树状态对 T-M4-01 的影响

当前工作树含 **T-M4-04d/e** 的未提交改动，其中 6 个文件直接修改了 T-M4-01 的代码：

| 文件 | 改动量 | 影响 |
|---|---|---|
| `service.py` | +217 行（`parse_image` 方法） | 新增 T-M4-04e 图片解析；T-M4-01 的 interpret/validate/execute/report 路径 **零改动** |
| `provider.py` | +66 行（`complete_with_images` 方法 + Protocol 扩展） | 新增视觉模型支持；`complete()` **零改动** |
| `routes.py` | +36 行（`/ai/parse-image` 端点） | 新增路由；现有端点 **零改动** |
| `schemas.py` | +33 行（`AIParseImageRequest/Response`） | 新增 schema；现有 schema **零改动** |
| `prompts.py` | +65 行（`IMAGE_RECEIPT_PARSE` 模板） | 新增 prompt；现有模板 **零改动** |
| `config.py` | +13 行（`ai_vision_model`/`ai_image_max_bytes`） | 新增配置字段；现有字段 **零改动** |

**结论：** T-M4-04e 的改动是纯增量（追加到文件末尾或新增字段），**未修改任何 T-M4-01 的现有代码路径**。T-M4-01 的 142 项测试在工作树状态下全绿，证明无回归。

但 T-M4-04e 改动引入了 ruff 违规（N-1），提交前必须修复。

---

## 七、结论

**T-M4-01 实现层：通过。** 01a/01b/01c/01d 四个子任务的验收点全部达成，前三轮登记的 13 项问题（P1×2、P2×4、P3×7）全部关闭，代码质量高——脱敏日志、降级契约、限流配额、用量审计（含重试 tokens 累计）、production 拒绝启动、提案校验平价、`content: null` 鲁棒性、超时上界等关键安全/正确性属性均有代码与测试支撑。T-M4-01 专属 142 项测试全绿。

**T-M4-01 交付层：有条件通过。** 存在两项非 01 模块但直接影响 01 门禁的问题：

1. **N-2（CI server pytest error）**：最新 CI run `34042066911` 的 server pytest 步骤因 `test_main.py::test_redoc_available` error 而 exit 1。此 error 非 T-M4-01 引入，但它使 T-M4-01 的测试结果在 CI 上不被认可。需 T-M4-04c lifespan 修复或 conftest schema 补全后才能恢复门禁。
2. **N-1（T-M4-04e ruff 违规）**：工作树 T-M4-04e 改动有 9 个 ruff error，提交后将破坏 server CI lint 门禁。

**优先级建议：**

1. **必做（恢复 server CI 门禁）：** N-2（conftest 补 `attachments` 表 或 lifespan graceful degradation）
2. **提交 T-M4-04e 前必做：** N-1（`ruff check --fix` + 手动修复 `F821`）
3. **可挂账：** N-3（成本计算精度）、N-4（fence 同行解析）、O-1~O-9

**门禁汇总：**

| 项 | 状态 |
|---|---|
| 本机 pytest（T-M4-01 六文件 142 项） | ✅ 通过 |
| 本机 pytest（全套，工作树） | ⚠️ 415 passed, 7 failed（T-M4-04e 测试）, 1 error（Windows 环境） |
| 本机 ruff（工作树） | ❌ 9 errors（T-M4-04e 改动引入，N-1） |
| 本机 ruff（HEAD `05f69ed`） | ✅ 通过 |
| 远端 CI 最近成功 | ✅ run `34040351268`（`a6f5c88`）三 job 全绿 |
| 远端 CI 最新 run | ❌ run `34042066911`：Client test 失败 + Server pytest error |
| 容器部署（Dockerfile + compose） | ✅ 理论上可构建（R-M4-39 已修复，未实测） |
