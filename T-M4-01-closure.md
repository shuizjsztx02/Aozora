# T-M4-01 收尾报告

**任务：** AI Gateway 基建收口（01a/01b/01c 全量验收 + 01d 全量收口）
**状态：** ✅ 完成（01a/01b/01c/01d 全 ✅）
**完成时间：** 2026-09-06
**基线：** 依据 T-M4-01 评审报告（97 项测试基线）执行剩余缺口（**R-M4-07 注：** 该轮评审报告未落盘 `docs/review/`，"97 项基线"无法回溯核对；已落盘的重新审查见 `docs/review/T-M4-01-review.md`）

## 前置背景（评审结论）

评审结论：T-M4-01 实际完成度 ~85–90%，主要问题在治理层面（文档状态失真、ADR 收口项遗漏、跨语言平价未自动化、production key 策略未对齐）。本收口基于评审 P0→P1→P2→P3 优先级清单执行。

## 已完成的收口项

### P0 — 对齐清理（4 项）

| # | 动作 | 文件 |
|---|---|---|
| 1 | **production 缺 key 拒绝启动**：`_check_production_safety` 新增 `AI_PROVIDER_API_KEY` 校验（仅 `env=production` 生效；dev/staging 保持 503 降级不变） | `server/src/common/config.py`、`server/src/main.py`（注释同步） |
| 2 | **端点命名对齐**：文档 `/ai/parse` → `/ai/interpret`（仅一行，实现 + 97+ 测试已使用 interpret） | `docs/milestones/production-execution-plan.md` 第 599 行 |
| 3 | **文档状态纠正**：01a/01b/01c 全标 ✅，01d 标 🔨 并明示阻塞项；父行 571 同步；**顺带修复** 03b 行（与父行 573 矛盾的状态） | `docs/milestones/production-execution-plan.md` 第 571、596–599、615 行 |
| 4 | **pyc/CI**：确认 `git ls-files \| grep pyc` 计数为 0；`.gitignore` 已含 `__pycache__/` `*.pyc`；CI 是全新 checkout 无污染路径 → 不需改动 | 无文件变更 |

### P1 — 解除下游阻塞（1 项）

| # | 动作 | 文件 |
|---|---|---|
| 5 | **ADR-003 WebView AI 调用通道**：桥命令 `ai.interpret/report/validate/execute`；Kotlin `AuthClient.ensureValidAccessToken()` 注入 Bearer，token 不出 Kotlin 层；OkHttp `callTimeout=25s`；TS `NativeBridge.call` per-command 超时提升到 25s；12s 客户端 UI 慢速提示；降级映射对齐服务端契约；同时拒绝 3 个替代方案（WebView 直连 / 客户端本地 SDK / 独立 IPC）。`ai-architecture.md` 新增 1 行指针。 | `docs/adr/ADR-003-webview-ai-channel.md`、`docs/architecture/ai-architecture.md` |

### P2 — 技术缺口（2 项）

| # | 动作 | 文件 |
|---|---|---|
| 6 | **12s 慢速提示服务端可观测性**：`SLOW_HINT_SECONDS = 12.0` 常量 + `_log_slow_if_needed()` 在 `interpret()` / `_generate_narrative()` 成功路径触发 `outcome: slow` 警告日志。客户端 12s 慢速 UX 提示在 ADR-003 中写明由 TS 计时器实现（服务端无推送通道） | `server/src/modules/ai/service.py`、`server/tests/test_ai_gateway.py::TestSlowHint`（2 项） |
| 7 | **跨语言平价测试**：循环读取 `contracts/action-proposal/fixtures/` 全部 16 个共享 fixture，断言 Python `validate_proposal` 的 valid/invalid 结论与 TS/ajv 一致；含 `test_all_fixtures_discovered` 防止 fixture 漂移。根级别 required 错误路径格式（jsonschema 输出 `$` vs ajv 输出属性名）已记录为契约允许差异 | `server/tests/test_ai_proposal_parity.py`（17 项） |

### P3 — 死代码处置（1 项）

| # | 动作 | 文件 |
|---|---|---|
| 8 | **AIConversation 处置**：保留模型（0001_initial 迁移已建表，T-M2-02 schema 基建），docstring 明确"为未来多轮上下文预留"，因 T-M4-02c 助手对话尚未启动，当前无消费方；删除会破坏 model/migration 一致性，实现多轮逻辑属投机性功能（违反 CLAUDE.md 最小改动原则） | `server/src/models/ai.py` |

## 测试结果

> **⚠️ 2026-09-06 订正（R-M4-02，依据 docs/review/T-M4-01-review.md 实测）：** 本节原数字失真——原称完整套件 "352 passed, 1 skipped"、`test_ai_gateway.py` "66 passed"、`test_auth.py` "32"、`test_config.py` "12"，且明细自求和（149）与标题（139）矛盾；"未处理项"中 `test_migrations.py` "2 项失败需独立 PR" 在复核实测为 **12 passed**（已由 T-M4-02b 的提交解决：`test_14_tables_registered` 断言 14 表、迁移列表含 0004–0008）。以下按实测订正。

### 我修改/新增文件相关测试：142 项全绿（第 2 轮修复后 2026-09-06 复测）

```
tests/test_config.py                14 passed
tests/test_auth.py                  40 passed（含新增 test_ai_key_missing_rejected_in_production）
tests/test_main.py                   6 passed
tests/test_ai_gateway.py            49 passed（首轮回审基线 46；T-M4-02 提交 +2；R-M4-06 修复新增 test_retry_accumulates_tokens_from_invalid_json_attempt +1）
tests/test_ai_proposal_parity.py    17 passed（新增）
tests/test_ai_proposal_validation.py 16 passed
```

### 完整测试套件：364 passed，1 skipped，1 error（首轮回审基线 HEAD f71e771 + M4 工作树实测）

error 为 `test_stats_exports.py::test_startup_cleanup_removes_stale_files` 的 Windows Temp 目录 `PermissionError`（环境限制、exports 模块、与 AI Gateway 无关，ubuntu CI 不复现）；skip 为同文件 POSIX 权限位用例。原"排除 2 项预存失败"的说法不成立：`test_migrations.py` 实测 12 passed，无失败。

### 新增测试明细（共 20 项）

| 测试 | 文件 | 类型 |
|---|---|---|
| `test_ai_key_missing_rejected_in_production` | `tests/test_auth.py` | production 启动拒绝 |
| `TestSlowHint::test_slow_request_logs_warning` | `tests/test_ai_gateway.py` | 慢速请求产生 WARNING 日志 |
| `TestSlowHint::test_fast_request_no_slow_log` | `tests/test_ai_gateway.py` | 快速请求无慢速日志 |
| `test_parity_with_ts` × 16 | `tests/test_ai_proposal_parity.py` | 跨语言平价（每 fixture 1 项） |
| `test_all_fixtures_discovered` | `tests/test_ai_proposal_parity.py` | 防 fixture 漂移 |

## 关键决策记录

| 决策点 | 选择 | 理由 |
|---|---|---|
| production 缺 key | 改代码对齐文档（拒绝启动） | 01a 验收标准原文"production 缺 key 拒绝启动用例"，且 dev/staging 503 降级理由（"让无密钥环境可运行"）在限定到非 production 后依然成立 |
| 端点命名 | 改文档（`/ai/parse` → `/ai/interpret`） | 实现 + 97+ 测试已使用 interpret；加别名增加表面积无收益 |
| AIConversation | 保留 + docstring 占位 + 记录延迟 | 0001_initial 已建表；T-M2-02 schema 基建；删除破坏 migration 一致性；多轮上下文属投机性功能（无产品消费方） |
| 12s 慢速提示 | 服务端只记 observability 日志；UX 提示归属客户端（ADR-003 明确） | 服务端无推送通道（POST 单响应）；客户端 UI 计时器是实现"12s 显示慢速提示"的唯一可行路径 |
| 根级别 required 错误路径 | 允许 `$` 与属性名并存 | jsonschema 与 ajv 对根级别缺失属性的路径格式化不同；valid/invalid 结论一致；TS 侧平价测试已断言精确路径，Python 侧聚焦 valid 结论平价 |

## 未处理项（明确记录，不在本次范围）

| 项 | 归属 | 理由 |
|---|---|---|
| `tools/scan-secrets.sh` 产物密钥扫描 | T-M5-02c | 评审清单已将其归属到 T-M5-02c，01d 验收已明确"归属 T-M5-02c 独立追踪" |
| `test_migrations.py` 两项失败 | 预存工作树问题 | ~~需独立提交修复迁移表计数（13→14）与 migration 文件~~ **已解决（R-M4-02 订正）：** 复核实测 `test_migrations.py` 12 passed——14 表断言、0004–0008 迁移与 `0008_proposal_idempotency.py` 已随 T-M4-02b 提交入库，无需独立 PR |
| `test_stats_exports.py::test_startup_cleanup_removes_stale_files` | Windows 环境问题 | `C:\Users\admin\AppData\Local\Temp\pytest-of-admin` 权限错误；与代码无关 |

## DashScope 真实密钥冒烟测试（01d 收口）

**时间：** 2026-09-06 16:51
**Provider：** DashScope（Alibaba Bailian），`https://dashscope.aliyuncs.com/compatible-mode/v1`
**Model：** `qwen3.7-flash`
**输入：** "午饭 十二块五"（使用真实 INTERPRET_TASK prompt 模板，`ledger_parse` 任务）

**结果：**
- ✅ HTTP 200 — API 密钥认证通过
- ✅ 返回 JSON（94 字符）：`{"type":"expense","amountCents":1250,"category":"餐饮","note":"","bizDate":"","confidence":0.95}`
- ✅ 语义正确：12.5 元 → amountCents=1250（分），置信度 0.95
- ✅ Token 统计：prompt=265, completion=566
- ✅ JSON 解析：`_parse_json_output` 路径成功

**结论：** DashScopeProvider 代码路径（httpx async + Bearer + OpenAI 兼容）在真实生产 provider 下工作正常。T-M4-01d 真实密钥冒烟测试通过。

## T-M4-01 最终状态（按子任务）

| 子任务 | 状态 | 备注 |
|---|---|---|
| T-M4-01a Provider 适配与模块骨架 | ✅ 完成 | production 拒绝启动测试通过 |
| T-M4-01b Prompt registry 与服务端提案校验 | ✅ 完成 | 17 项跨语言平价测试 + 16 项 schema/幻觉/低置信验证测试 |
| T-M4-01c 限流、用量审计与降级 | ✅ 完成 | 12s 慢速提示可观测性；AIConversation 多轮上下文延迟至 T-M4-02c |
| T-M4-01d 网关 API 与端到端收口 | ✅ 完成 | DashScope 真实密钥冒烟通过；产物密钥扫描归属 T-M5-02c |

**父任务 T-M4-01：✅ 全部完成**

## 后续建议

1. **近期**：T-M5-02c 完成产物扫描（`tools/scan-secrets.sh`）→ 安全线收口
2. **下游解锁**：ADR-003 已就绪 → T-M4-02c（助手对话流 + 确认卡 UI）与 T-M4-03c（客户端洞察展示）可并行启动
3. ~~**清理**：`ProposalIdempotency` 工作树残留建议独立 PR 修复 migration 测试（非 M4-01 职责）~~ **已解决：** migration 测试随 T-M4-02b 提交修复（12 passed），无需独立 PR
