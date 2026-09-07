# T-M4-01 重新审查报告（第 1 轮）

**审查日期：** 2026-09-07
**审查范围：** T-M4-01 全量（01a 基建 + 01b 平价 + 01c 验收 + 01d 收口），含 closure 报告
**审查方法：** 源码逐行审查 + 测试覆盖度分析 + 文档一致性核对
**审查文件：** `service.py`（1237 行）、`router.py`（177 行）、`provider.py`（225 行）、`validator.py`（74 行）、`test_ai_gateway.py`（700 行）、`test_ai_proposal_parity.py`（88 行）、`config.py`、`main.py`、`ADR-003`、`test_ai_proposal_validation.py`

---

## 总体评价

**实现质量：** 良好。核心 failure contract（retry-once、timeout-never-retry、degrade-gracefully）实现正确，与 APP-CONTEXT §6.4 契约一致。代码注释密度高，每个决策都有理由链。retry token 累积（R-M4-06）和 narrative cache 降级逻辑经过多轮修复，已经稳定。

**主要风险点：** Token 审计一致性（P1）、测试覆盖盲区（P1）、`_generate_narrative` 的 retry token 计算虽然正确但可读性差（P2）。

---

## P0 — 阻塞级问题

无。

---

## P1 — 应在合入前修复（2 项）

### 1-1 🔴 Token 审计一致性缺口：`interpret()` / `parse_image()` 在 retry 路径丢失 token 记录

**文件：** `server/src/modules/ai/service.py`

**问题：** 三个调用 LLM 的方法对 retry token 的记录策略不一致：

| 方法 | Provider 5xx 后 raise | JSON parse 失败后 raise | Token 记录 |
|---|---|---|---|
| `interpret()` | raise 503，**不记录** | raise 503，**不记录** | 仅成功路径 |
| `parse_image()` | raise 503，**不记录** | raise 503，**不记录** | 仅成功路径 |
| `_generate_narrative()` | return（无 narrative），**记录** | return，**记录** | 成功 + 失败路径 |

`_generate_narrative` 在 R-M4-10 修复后正确地在降级路径记录 token。但 `interpret()` 和 `parse_image()` 在 retry 到达 provider（消耗了 token）后最终 raise 时，这些 token **永远不会出现在 AIUsage 表中**。

**具体路径：**
- `interpret()` L191-196：`ProviderError` → raise → DB session rollback → 无 usage 行
- `interpret()` L208-214：JSON parse 失败 → raise → 同上
- `parse_image()` L1075-1081, L1089-1094：同 `interpret()`

**影响：** Provider 账单会扣费，但我们的审计表无记录。长期运行会导致成本对账差异。结构化日志（`outcome: provider_5xx` + `attempt`）可作为补充数据源，但 DB 行才是正式审计。

**建议修复：** 在 `interpret()` 和 `parse_image()` 的 retry 循环中，当 raise AppException 前，先 flush 一个 AIUsage 行（tokens 来自 retry 累积）。可以提取一个 `_record_failure_usage()` 辅助方法复用。

### 1-2 🔴 `parse_image()` 缺少独立的集成测试

**文件：** `server/tests/test_ai_gateway.py`

**问题：** `parse_image()` 方法有 150+ 行实现（L985-1165），覆盖：
- attachment 所有权 + 状态校验（EXPIRED → 410, not PENDING → 409）
- 文件存在性检查（→ 404）
- 图片大小限制（→ 413）
- 图片解析禁用（max_bytes=0 → 503）
- Vision model retry/parse 循环
- Category 名称→ID 解析（含 fuzzy prefix fallback）
- bizDate 时区感知 fallback
- Attachment 状态更新 → PARSED
- 完整 ActionProposal 构造

但 **零个** 测试覆盖这个路径。`test_ai_gateway.py` 中 `TestParseImage` 类不存在。`test_parse_image_category_fallback`、`test_parse_image_empty_biz_date_fallback` 等 parity test 在 closure 报告中提到，但实际不在测试文件中。

**影响：** 这是 T-M4-04e 新增的功能路径，虽然严格来说是 04e 的范畴，但代码在 `service.py` 的 `AIService` 中，且 closure 报告声称这些测试存在。

**建议：** 至少补充 3 个核心路径测试：
1. 成功路径（happy path：图片解析 → proposal 返回）
2. 状态校验（EXPIRED → 410, not PENDING → 409）
3. Category 解析 + bizDate fallback

---

## P2 — 应在后续迭代修复（4 项）

### 2-1 🟡 `_generate_narrative` 的 retry token 计算可读性差，易被误判为 bug

**文件：** `server/src/modules/ai/service.py` L928-937

**现状：**
```python
await self._record_usage(
    user, result, model, request_id,
    extra_prompt_tokens=retry_prompt_tokens - result.prompt_tokens,
    extra_completion_tokens=retry_completion_tokens - result.completion_tokens,
)
```

这个减法（`retry_... - result....prompt_tokens`）在数学上是正确的（我逐路径验证了），但读起来非常反直觉。注释解释了"retry_*_tokens includes this last attempt"，但需要读者自行做代数才能验证。

**建议：** 在 retry 循环前初始化 `retry_prompt_tokens = 0`，在每次 `except ValueError` 中先累加再 break/continue。到 JSON 最终失败时，`retry_prompt_tokens` 已包含所有失败 attempt（含最后一次）。然后直接用 `extra_prompt_tokens=retry_prompt_tokens - result.prompt_tokens` 改为 `extra_prompt_tokens=retry_prompt_tokens_before_last_attempt`（在累加前先保存旧值）。这样语义更清晰。

或者更简单：统一为 "extra = 所有失败 attempt 的 token，不含最后一次"——与 `interpret()` 对齐，降低认知负担。

### 2-2 🟡 `_log_failure` 与 `_log_success`/`_log_slow_if_needed` 的 provider 访问模式不一致

**文件：** `server/src/modules/ai/service.py`

- `_log_success` L370：`assert self._provider is not None` → `self._provider.name`（直接访问）
- `_log_failure` L402：`getattr(self._provider, "name", None)`（安全访问）
- `_log_slow_if_needed` L436：`getattr(self._provider, "name", None)`（安全访问）

`_log_failure` 用 `getattr` 是因为 provider 可能为 None（no_provider 路径）。但 `_log_slow_if_needed` 也被 `_log_failure` 调用（L412），此时 provider 可能也是 None，所以用 `getattr` 是正确的。

但 `_log_success` 用 `assert` 而不是 `getattr`——如果未来有人在 provider=None 的路径上误调 `_log_success`，会拿到 AssertionError 而不是 None。这不是 bug，但风格不统一。

**建议：** 保持现状即可（assert 是正确的防御），但在 `_log_failure` 的 docstring 中注明 "provider may be None for no_provider failures"。

### 2-3 🟡 Narrative cache 的 O(n) 驱逐

**文件：** `server/src/modules/ai/service.py` L120-123

```python
oldest = min(_narrative_cache.items(), key=lambda kv: kv[1][0])[0]
```

`_MAX_NARRATIVE_CACHE_ENTRIES = 500`，每次满时遍历全部 500 项找最旧的。Python dict 保持插入顺序，可以用 `next(iter(cache))` O(1) 获取最早插入的项（但需注意 TTL 过期后可能不是最旧的）。

**影响：** 500 项的 O(n) 在绝对值上很小（<1ms），不构成性能问题。但如果后续扩大缓存规模，值得替换。

**建议：** 标记为 tech-debt，暂不修复。

### 2-4 🟡 `request_id[:64]` 截断无日志警告

**文件：** `server/src/modules/ai/service.py` L357

```python
request_id=request_id[:64],
```

注释解释了原因（X-Request-Id 是客户端输入，不可控），但截断时无日志。如果客户端发送 >64 字符的 request_id，截断后的碰撞概率虽低但非零。

**建议：** 在截断时发一条 DEBUG 日志。

---

## P3 — 记录备忘（3 项）

### 3-1 Rate limiter 单实例限制

**文件：** `server/src/modules/ai/service.py` L58-64

代码注释已明确说明："replace with Redis when deploying multiple instances"。记录在案，T-M5-05b 解决。

### 3-2 `validate_proposal` 的 `isinstance(proposal, dict)` fallback

**文件：** `server/src/modules/ai/service.py` L482

```python
proposal_dict = proposal if isinstance(proposal, dict) else {}
```

Schema 验证通过后 `proposal` 必定是 dict（schema 要求 `type: object`），所以 `else {}` 是死路径。但作为防御性编码可以保留。

### 3-3 `_resolve_categories` 的 prefix matching 顺序依赖

**文件：** `server/src/modules/ai/service.py` L1215-1218

prefix 匹配的结果依赖 `name_to_id` 的迭代顺序（dict 插入顺序）。如果两个 category 名字互为前缀（如"餐饮"和"餐饮-外卖"），匹配结果取决于哪个先被插入。当前来自 DB 查询，顺序由 category 创建时间决定，非确定性但实际影响很小。

---

## 测试覆盖度评估

### 已覆盖（✅）

| 模块 | 测试数 | 评价 |
|---|---|---|
| `interpret()` happy path | 4 | 充分 |
| `interpret()` provider 5xx/429/retry | 5 | 充分 |
| `interpret()` timeout | 2 | 充分 |
| `interpret()` invalid JSON retry | 4 | 充分 |
| `interpret()` no-provider degrade | 1 | OK |
| `_parse_json_output` | 6 | 充分 |
| Rate limit | 3 | 充分 |
| Daily quota | 3 | 充分（含时区边界） |
| Retry token accumulation | 5 | 充分（含 invalid_json 路径） |
| Slow hint | 2 | OK |
| Config validation | 2 | OK |
| Schema validator parity | 17 | 充分（含 fixture drift guard） |
| Proposal context validation | 13 | 充分（含 hallucination、low confidence、S-C1 fix） |
| `local_day_start_utc` | 4 | 充分 |
| `execute_proposal` | 4 | 充分（含 idempotency、race、low confidence gate） |

### 未覆盖（❌）

| 模块 | 行数 | 风险 |
|---|---|---|
| `parse_image()` | ~150 行 | **高** — 含 attachment 状态机、vision model、category 解析 |
| `_generate_narrative()` 独立测试 | ~100 行 | **中** — 通过 `generate_report` 间接覆盖了 happy path + degradation，但 number verification failure 路径未独立测试 |
| `_resolve_categories()` 独立测试 | ~30 行 | **低** — 通过 `parse_image` 间接覆盖（如果补了 parse_image 测试） |
| Narrative cache 行为测试 | — | **低** — 没有测试验证 cache hit/miss/TTL 行为 |

---

## 文档一致性

| 文档 | 状态 | 评价 |
|---|---|---|
| `T-M4-01-closure.md` | ✅ | 结论准确，测试数字已订正（R-M4-02），决策记录完整 |
| `ADR-003` | ✅ | 桥命令、token 隔离、超时预算、降级映射齐全 |
| `production-execution-plan.md` | ✅ | 状态标记与 closure 一致 |
| `ai-architecture.md` | ✅ | 有 ADR-003 指针 |

**一个不一致：** closure 报告第 61 行声称 "新增测试明细（共 20 项）"，但表中实际列出 5 项（2 个 slow hint + 1 个 production key + 16 个 parity + 1 个 fixture drift = 20）。数字正确，但标题"共 20 项"与表中 5 行的呈现方式可能造成误解（实际是 20 个 test case，5 个 test function）。

---

## 结论

| 等级 | 数量 | 处置 |
|---|---|---|
| P0 阻塞 | 0 | — |
| P1 修复 | 2 | 1-1 应在合入前修复（token 审计一致性）；1-2 至少补充 `parse_image` 的 3 个核心测试 |
| P2 后续迭代 | 4 | 可读性 + 风格 + 小改进 |
| P3 记录 | 3 | 已知限制，有 TODO |

**建议：** P1 修复后可合入。P1-1（token 审计一致性）是影响成本对账的正确性问题，优先级高于 P1-2（测试覆盖）。
