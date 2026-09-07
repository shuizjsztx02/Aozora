# T-M4-01 审查报告（第 5 轮：最终重审）

- **审查日期：** 2026-09-07
- **审查模式：** Review（只读；不修改项目代码/测试/配置）
- **审查对象：** T-M4-01 模块（AI Gateway 基建）当前实现状态
- **基线：** HEAD `b4e0c39`（"fix(server): T-M4-01 round4 审查修复 — ruff / 测试 / 成本 / JSON 解析"）
- **依据：** `docs/milestones/production-execution-plan.md`、`docs/architecture/ai-architecture.md`、`APP-CONTEXT §6.4/§10.3`、前四轮审查报告、`T-M4-01-closure.md`
- **范围：** `server/src/modules/ai/` 全目录、`server/src/common/config.py`（AI 段）、`server/src/main.py`（AI 启动/关闭段）、`server/src/models/ai.py`、AI 相关测试、`server/Dockerfile`、`docker-compose.yml`、`server/pyproject.toml`、`.github/workflows/ci.yml`、`docs/adr/ADR-003-webview-ai-channel.md`
- **排除：** 客户端 `app/`、真实 DashScope 冒烟、T-M4-02/03 业务逻辑细节

---

## 一、验证命令与实测结果

| 命令 | 结果 | 备注 |
|---|---|---|
| `git log --oneline -1` | `b4e0c39` | HEAD 已推进到 round4 修复提交 |
| `git status --short` | 15 modified, 2 untracked | 工作树改动全部属于其他任务（T-M4-03/04/05），T-M4-01 代码路径零改动 |
| `pytest tests/test_ai_gateway.py test_ai_proposal_parity.py test_ai_proposal_validation.py tests/test_config.py test_auth.py test_main.py -q` | **142 passed** | T-M4-01 六文件全绿，与 round4 一致 |
| `pytest tests/ -q --tb=no` | **424 passed, 2 skipped** | 全套测试全绿（无 failed/error），skipped = Windows 环境 POSIX 权限测试 |
| `python -m ruff check src/ tests/`（本地 ruff 0.8.6） | **All checks passed!** | 0 errors |
| `python -m ruff check src/ tests/`（CI ruff 0.16.6） | **1 error** | `I001` import 排序：`tests/test_stats_exports.py:560`（T-M2-05 文件，非 T-M4-01）|
| `gh run list --limit 5` | 最近 5 个 run **全失败** | 最新 run `34043998986`（`b4e0c39`）server ruff 步骤因 `test_stats_exports.py` I001 失败；Android 2 项测试失败（WidgetDataHelperTest）|

**只读边界确认：** 本次审查未修改任何项目代码、测试、配置或依赖；仅运行只读检查与测试，并新建本审查文档。

---

## 二、前四轮发现复核

### Round 1–3 发现（R-M4-01~07, R-M4-38~43）

| # | 标题 | 级别 | 状态 |
|---|---|---|---|
| R-M4-01 | 实现全量未提交 | P1 | ✅ 已关闭（多轮提交入库） |
| R-M4-02 | closure 报告测试事实失真 | P2 | ✅ 已关闭（数字已订正） |
| R-M4-03 | interpret 契约与文档脱节 | P2 | ✅ 已关闭（ai-architecture.md 更新） |
| R-M4-04 | ruff 依赖未声明 | P2 | ✅ 已关闭（pyproject.toml 已声明） |
| R-M4-05 | validator.py docstring 引用不存在的 assembly.py | P3 | ✅ 已关闭 |
| R-M4-06 | 重试路径 tokens 未计入 AIUsage | P3 | ✅ 已关闭（extra_*_tokens 累加） |
| R-M4-07 | 评审报告未落盘 | P3 | ✅ 已关闭（多份报告已归档） |
| R-M4-38 | CI server job 完全失效（exit 4） | P1 | ✅ 已关闭（filterwarnings 修复） |
| R-M4-39 | Dockerfile 未 COPY contracts/ | P2 | ✅ 已关闭 |
| R-M4-40 | `content: null` → AttributeError → 500 | P3 | ✅ 已关闭 |
| R-M4-41 | 叙述缓存 key book_id 大小写不一致 | P3 | ✅ 已关闭 |
| R-M4-42 | `ai_timeout_seconds` 无上界 | P3 | ✅ 已关闭（`le=12.0`） |
| R-M4-43 | `_generate_narrative` 同族 tokens 未累计 | P3 | ✅ 已关闭 |

### Round 4 发现（N-1~N-4）

| # | 标题 | 级别 | 状态 | 验证 |
|---|---|---|---|---|
| N-1 | T-M4-04e ruff 违规（9 errors） | P2 | ✅ 已关闭 | `b4e0c39` 修复；本地 ruff 0 errors |
| N-2 | CI server pytest error（attachments 表） | P2 | ✅ 已关闭 | `b00c9c9` 修复；本地全套 424 passed |
| N-3 | `_record_usage` 成本整数除法截断 | P3 | ✅ 已关闭 | `service.py:347` 改为 `round(total_tokens * price / 1_000_000)` |
| N-4 | `_parse_json_output` 不处理 fence 同行内容 | P3 | ✅ 已关闭 | `service.py:304-305` 新增同列回退路径 |

**前四轮共 17 项发现全部关闭。**

---

## 三、T-M4-01 验收标准逐项确认（HEAD `b4e0c39`）

### T-M4-01a Provider 适配与模块骨架 — ✅ 达成

| 验收点 | 代码位置 | 结果 |
|---|---|---|
| provider adapter 接口 + DashScope（OpenAI 兼容，httpx async） | `provider.py:75-157` | ✅ 连接池复用，`aclose()` 关闭 |
| Settings 扩展（模型/超时/重试，key 仅环境变量） | `config.py:110-174` | ✅ `AliasChoices` + 默认值 |
| production 缺 key 拒绝启动 | `config.py:203-207` | ✅ `_check_production_safety` |
| dev/staging 缺 key 503 降级 | `main.py:73-77`、`service.py:165-169` | ✅ 启动警告 + 运行时 503 |
| mock provider | `provider.py:84` transport 注入点 | ✅ |
| `ai_timeout_seconds` 上界 | `config.py:137` `le=12.0` | ✅ |
| `content: null` 鲁棒性 | `provider.py:147-148` | ✅ 非 str → ProviderError |

### T-M4-01b Prompt registry 与服务端提案校验 — ✅ 达成

| 验收点 | 代码位置 | 结果 |
|---|---|---|
| Prompt registry（版本化、按任务） | `prompts.py:19-143` | ✅ `PromptTemplate` + `REGISTRY` |
| LLM 输出 JSON 解析（容忍 markdown fence） | `service.py:274-322` | ✅ 快速路径 + fence 回退 + 同行回退（N-4 修复）|
| Python ActionProposal 校验器（Draft-07 + format: date） | `validator.py:37-73` | ✅ |
| 跨语言平价测试 | `test_ai_proposal_parity.py`（17 项） | ✅ 含漂移守卫 |
| 非法 JSON 重试一次、仍失败降级 | `service.py:182-214` | ✅ MAX_ATTEMPTS=2 |

### T-M4-01c 限流、用量审计与降级 — ✅ 达成

| 验收点 | 代码位置 | 结果 |
|---|---|---|
| 用户级限流 | `service.py:238-254` | ✅ 内存桶 + 60s 窗口 |
| 配额（guest 日配额） | `service.py:256-269` | ✅ 按用户时区日起算 |
| AIUsage append-only 审计（含重试 tokens） | `service.py:326-360` | ✅ `extra_*_tokens` 累加 |
| 成本计算精度 | `service.py:347` | ✅ `round()` 替代整数除法（N-3 修复）|
| 超时语义（12s 慢速 / 25s 取消） | `service.py:81`、`:414-440` | ✅ |
| provider 故障 → 降级响应 | `service.py:190-196` | ✅ 503 `ai_unavailable` |
| 脱敏日志 + requestId 贯穿 | `service.py:362-440` | ✅ 仅元数据 |

### T-M4-01d 网关 API 与端到端收口 — ✅ 达成

| 验收点 | 代码位置 | 结果 |
|---|---|---|
| `POST /ai/interpret` + Bearer 鉴权 | `routes.py:35-50` | ✅ |
| ADR-003 WebView AI 通道 | `docs/adr/ADR-003-webview-ai-channel.md` | ✅ |
| 端到端测试 | `test_ai_gateway.py`（49 项） | ✅ |
| Dockerfile contracts/ 拷贝 | `Dockerfile:37` | ✅ |
| docker-compose 构建上下文 | `docker-compose.yml:24-26` | ✅ |

---

## 四、新发现问题

### 🟡 NEW-1（P2 · CI/工程）— CI ruff 版本（0.16.6）比本地（0.8.6）更严格，`test_stats_exports.py` I001 违规阻塞 server 门禁

- **状态：** 未修复
- **文件与位置：** `server/tests/test_stats_exports.py:560`（T-M2-05 文件）
- **问题与触发条件：** CI 安装 ruff 0.16.6（`pyproject.toml` 声明 `ruff>=0.8,<1`，允许跨 8 个次版本升级），该版本对函数体内 import 块的排序规则比本地 0.8.6 更严格。`test_stats_exports.py:560-562` 的函数内 import（`shutil` / `tempfile` / `src.modules.exports.service`）在 0.16.6 下被判 I001 违规，需要空行分隔 stdlib 和第三方 import。本地 0.8.6 不报此错误。
- **证据或复现命令：** CI run `34043998986` → Server Lint → `Found 1 error`（`I001 tests/test_stats_exports.py:560`）
- **影响：** server CI lint 门禁失败，导致 server pytest 步骤不执行；T-M4-01 的 142 项测试虽然在本地全绿，但 CI 无法确认。**该违规不属于 T-M4-01 代码**（属于 T-M2-05 exports 模块），但它阻塞了 T-M4-01 的远端门禁。
- **修复建议：** 两种方案任选：① `ruff check --fix` 自动修复 import 排序（最简单）；② 锁定 ruff 版本为 `~=0.8.6` 避免跨版本漂移
- **复验方法：** CI server lint 步骤绿

### 🟢 NEW-2（P3 · CI/环境）— Android CI 2 项 WidgetDataHelperTest 失败

- **状态：** 未修复
- **文件与位置：** Android `WidgetDataHelperTest.loadData_sumsTodayExpensesOnly` / `loadData_excludesSoftDeleted`
- **问题与触发条件：** CI run `34043998986` Android job 68 tests completed, 2 failed。此为 Android 侧测试，与 T-M4-01 无关。
- **影响：** Android 门禁失败。不影响 T-M4-01 判定。
- **归属：** 非 T-M4-01 职责，记录备查

---

## 五、观察项（维持，非缺陷）

- **O-1** 平价测试为"共享 fixture + 镜像期望"，非实时跨语言校验。可接受为 v1 轻量方案。
- **O-2** 限流桶在 no-provider 503 路径仍计入。可辩护的突发保护行为。
- **O-3** 配额 check-then-act TOCTOU。单进程内存限流为主防线，影响可忽略。
- **O-4** httpx.AsyncClient 连接池已修复（`provider.py:92-95` 单例 `_client`）。✅
- **O-5** X-Request-Id 截断 64 与 column String(64) 对齐。
- **O-6** tzdata 依赖未显式声明。`python:3.12-slim` 默认包含。
- **O-7** Python 版本漂移（Dockerfile 3.12 / CI 3.13 / pyproject >=3.12）。
- **O-8** 配额计数的时钟漂移。影响可忽略。
- **O-9** `AIInterpretRequest.context` 未使用。任意嵌套 dict，受限于请求体大小。
- **O-10** ruff 版本区间 `>=0.8,<1` 过宽，CI 安装 0.16.6 引入本地不可见的违规。建议锁定或缩窄。

---

## 六、工作树状态对 T-M4-01 的影响

当前工作树 15 modified + 2 untracked 全部属于其他任务：

- `app/` 改动（T-M4-03/04/05 客户端）
- `docs/` 改动（里程碑文档、审查报告）
- `T-M4-03-final-summary.md`

**T-M4-01 的 6 个核心文件（service.py / provider.py / routes.py / schemas.py / prompts.py / config.py）在工作树中无未提交改动。** round4 的修复（`b4e0c39`）已完整覆盖 N-1~N-4。

---

## 七、结论

**T-M4-01 实现层：✅ 通过。**

01a/01b/01c/01d 四个子任务的验收点全部达成。前四轮登记的 17 项问题（P1×2、P2×5、P3×10）全部关闭。代码质量高——脱敏日志、降级契约、限流配额、用量审计（含重试 tokens 累计 + 成本精度修复）、production 拒绝启动、提案校验平价、`content: null` 鲁棒性、超时上界、JSON fence 同行回退等关键安全/正确性属性均有代码与测试支撑。

**T-M4-01 测试层：✅ 本地通过，远端有条件通过。**

- 本机 pytest（T-M4-01 六文件 142 项）：✅ 全绿
- 本机 pytest（全套 424 项）：✅ 全绿
- 本机 ruff（0.8.6）：✅ 全绿
- 远端 CI ruff（0.16.6）：❌ 1 error（`test_stats_exports.py` I001，非 T-M4-01 代码，NEW-1）
- 远端 CI server pytest：⚠️ 未执行（被 ruff 阻断）

**T-M4-01 交付层：有条件通过。** 唯一阻塞项是 NEW-1——CI ruff 版本漂移导致 T-M2-05 文件的 import 排序违规，阻断 server 门禁。修复极简（一行 `ruff check --fix`），但需在 T-M4-01 最终判定"完成"前完成。

**优先级建议：**

1. **必做（恢复 server CI 门禁）：** NEW-1（`ruff check --fix` 修复 `test_stats_exports.py` import 排序）
2. **可挂账：** NEW-2（Android 测试失败，非 T-M4-01）、O-10（ruff 版本区间缩窄）
3. **无需行动：** 其余观察项

**门禁汇总：**

| 项 | 状态 |
|---|---|
| 本机 pytest（T-M4-01 六文件 142 项） | ✅ 通过 |
| 本机 pytest（全套 424 passed, 2 skipped） | ✅ 通过 |
| 本机 ruff（0.8.6） | ✅ 通过 |
| 远端 CI ruff（0.16.6） | ❌ 1 error（NEW-1，非 T-M4-01 代码） |
| 远端 CI server pytest | ⚠️ 未执行（被 NEW-1 阻断） |
| 远端 CI 最近成功 | ✅ run `34040351268`（`a6f5c88`）三 job 全绿 |
| 容器部署（Dockerfile + compose） | ✅ 理论上可构建（R-M4-39 已修复，未实测） |

**T-M4-01 整体评定：实现完成、质量合格，唯一阻塞为跨任务 CI 环境问题（NEW-1，一行修复）。**
