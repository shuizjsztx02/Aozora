# T-M4-03 重新审查报告（第 8 轮）

- **审查日期：** 2026-09-07
- **审查范围：** T-M4-03a（事实包服务）+ T-M4-03b（叙述生成与数字校验）+ T-M4-03c（客户端洞察展示）
- **基线：** HEAD `44b7321` + 工作树改动（其他任务线，非 T-M4-03）
- **模式：** 全量代码审查 + 实测验证（独立重跑全部测试）
- **上一轮：** 第 7 轮（2026-09-07，通过）

---

## 一、实测结果（第 8 轮实际运行）

| 检查项 | 命令 | 结果 |
|---|---|---|
| 任务级服务端测试 | `pytest tests/test_fact_pack.py tests/test_narrative_report.py -q` | **37 passed** ✅ |
| 全量服务端测试 | `pytest tests/ -q --tb=no` | **426 passed, 2 skipped, 0 error** ✅ |
| 客户端 AiInsightCard 测试 | `npx vitest run src/__tests__/features/AiInsightCard.test.tsx` | **10 passed** ✅ |
| 客户端全量测试 | `npx vitest run` | **473 passed, 1 failed**（28 test files）|
| 客户端失败项 | `ImagePickerSheet.test.tsx` | T-M4-04e 范围，与 T-M4-03 无关 |

**较第 7 轮变化：** 服务端全量从 424→426 passed（+2，其他任务线增长）。客户端从 452→473 passed（+21，其他任务线增长），新增 1 个 T-M4-04e 失败（`ImagePickerSheet` auto-continue after permission granted），与 T-M4-03 完全无关。

---

## 二、安全审计（第 8 轮）

| 检查项 | 结果 | 代码位置 |
|---|---|---|
| LLM 数据隔离 | ✅ LLM 仅接触 `build_context_dict` 输出 | `narrative.py:74-142` |
| 数字溯源（data_references） | ✅ 逐一比对 fact key + value | `narrative.py:30-71` |
| 未声明数字检测 | ✅ 正则 `(?<!\d)(\d{3,})(?!\d)` 扫描 ≥100 整数 | `narrative.py:170-212` |
| 校验失败降级 | ✅ 丢弃叙述，返回纯事实包 200，不暴露内部错误 | `service.py:953-969` |
| 所有权校验 | ✅ `Book.owner_id == user_id AND deleted_at IS NULL` | `fact_pack.py:196-204` |
| 速率限制 | ✅ `_check_rate_limit` + `_check_daily_quota` | `service.py:869-883` |
| 限流降级 | ✅ 限流命中 → 200 + narrative=null（非 429） | `service.py:869-883` |
| SQL 注入 | ✅ 全部 SQLAlchemy ORM 参数化查询 | 所有 `_query_*` 方法 |
| 安全事件日志 | ✅ WARNING + `number_verification_failed` 标签 | `service.py:956-967` |
| Token 审计 | ✅ 所有路径记录 AIUsage（含重试/校验失败/invalid_json 耗尽） | `service.py:932-949` |
| Prompt 注入 | ✅ 用户输入仅在 `user_input` 中，系统提示独立且约束明确 | `prompts.py:57-87`, `service.py:889-893` |
| 客户端 balance 口径 | ✅ `income - expense`（有符号），与服务端一致 | `aggregator.ts:50-52` |
| Hash 碰撞风险 | ✅ 16 hex = 64 bit，500 条目下碰撞概率 <10⁻¹² | `fact_pack.py:175` |
| 缓存键一致性 | ✅ `normalized_book_id`（`str(uuid.UUID(input))`）与 `fact_pack.book_id`（`str(uuid_obj)`）均为小写规范形式 | `service.py:754-757`, `fact_pack.py:252` |

---

## 三、代码质量审查

### 3.1 服务端核心

| 模块 | 评估 | 备注 |
|---|---|---|
| `fact_pack.py` | ✅ 优 | 确定性数据聚合，hash 覆盖全部字段（含 tx_count、category_name、largest tx id/note/biz_date、trend transaction_count），R-M4-12 修复完整 |
| `narrative.py` | ✅ 优 | `_build_lookup` 复用 `build_context_dict`（消除重复），`_find_undeclared_numbers` 行为文档化，中文正则边界正确处理 |
| `service.py` | ✅ 优 | 叙述缓存 300s/500 条目；token 记录在 verification 前（R-M4-10）；限流降级为 200（R-M4-15）；retry 累加器覆盖 invalid_json 耗尽路径（R-M4-43） |
| `prompts.py` | ✅ 良 | "当月"措辞已修复（R-M4-14）；事实键名文档完整；系统提示明确要求 JSON + data_references |
| `routes.py` | ✅ 优 | `/ai/report` 端点依赖注入完整（db, provider, current_user） |
| `schemas.py` | ✅ 优 | `balance: int` 无 `ge=0` 约束，允许赤字 |

### 3.2 客户端

| 模块 | 评估 | 备注 |
|---|---|---|
| `AiInsightCard.tsx` | ✅ 良 | 缓存/离线/降级/加载/错误五态完整；`navigator.onLine` + `bridge.isAvailable()` 双重检查 |
| `bridge-types.ts` | ✅ 优 | `AiReportResponse` 与服务端 `AIReportResponse` schema 字段一致 |
| `AozoraBridge.kt` | ✅ 优 | `handleAiReport` 捕获 5 种 AiError 类型（Timeout/Network/RateLimited/Unavailable/AuthLost） |
| `AiGatewayClient.kt` | ✅ 优 | `report()` 使用 `executeAuthorized`，JWT 刷新链路完整 |
| `ReportsPage.tsx` | ✅ 优 | `AiInsightCard` 集成在月度概览下方 |

### 3.3 跨端一致性

| 对账点 | 结果 |
|---|---|
| balance 口径 | ✅ 服务端 `/ai/report.balance` = 服务端 `/stats/monthly.balance` = 客户端 `computeBalance` = `income - expense`（允许负值） |
| Parity 守卫 | ✅ `TestStatisticsParity` 4 个 HTTP 级测试锁死（monthly totals / deficit balance / trend / category sum） |
| 缓存行为 | ✅ 服务端 `narrative_cached` 标记 → 客户端展示"已缓存" |

---

## 四、新发现

### 1. 🟢 NEW-1（P4 · 文档）— `T-M4-03-final-summary.md` API 示例中叙述仍用"本月"

- **级别：** P4（示例文本，不影响功能或 prompt）
- **文件：** `T-M4-03-final-summary.md:158`
- **现状：** 示例响应中 `"narrative": "本月收支情况良好..."` 使用"本月"
- **分析：** 这是 LLM 输出的示例文本，不是 prompt 指令。实际 prompt（`prompts.py:69-72`）已使用"当月"（R-M4-14 修复）。LLM 生成的叙述措辞取决于模型本身，"本月"只是示例占位。
- **影响：** 零功能影响，仅文档阅读时可能引起困惑。
- **建议：** 下次文档刷新时改为"当月"。

### 2. 🟢 NEW-2（P4 · 代码风格）— `AiInsightCard` 直接调用 `bridge.call` 而未使用 `reportAI` 辅助方法

- **级别：** P4（风格/维护性）
- **文件：** `AiInsightCard.tsx:100`
- **现状：** `bridge.call('ai.report', { bookId, period }, { timeoutMs: 30000 })` 直接调用，而 `native-bridge.ts:315-316` 已有 `reportAI(bookId, period)` 辅助方法。
- **分析：** 功能等价（`reportAI` 内部就是同一调用），但存在两个超时定义点（组件内联 30000 vs `AI_TIMEOUT` 常量）。
- **影响：** 零功能影响。如未来修改超时或 API 路径，需改两处。
- **建议：** 低优先重构——改用 `bridge.reportAI(bookId, period)`。

---

## 五、门禁结论（第 8 轮）

**实现层（服务端 T-M4-03a/03b）：✅ 通过。**

- 37 个任务级测试全绿
- 全量 426 passed / 2 skipped / 0 error
- 安全链路完整：事实包隔离 → data_references 校验 → 未声明数字检测 → 降级不暴露内部错误
- Token 审计覆盖所有路径（成功/重试/校验失败/限流降级/invalid_json 耗尽）
- 4 个 HTTP 级 parity 测试守卫对账不变量

**跨端契约：✅ 通过。**

- 客户端 balance = `income - expense`（有符号 `number`），与服务端 `/stats/monthly` + `/ai/report` 口径一致
- `formatSigned` 正确显示赤字
- Android 侧 `handleAiReport` 完整处理 5 种错误类型

**客户端（T-M4-03c）：✅ 通过。**

- AiInsightCard 10 测试全绿
- localStorage 缓存 1h TTL
- `navigator.onLine` 离线降级
- `narrative_cached` 标记展示
- ReportsPage 集成完整

**交付层：✅ 通过。**

- HEAD `44b7321` 测试全绿（426 passed / 2 skipped / 0 error）
- 工作树改动均为其他任务线（T-M4-04e 等），T-M4-03 代码无未提交改动

**历史问题终态（10 项）：**

| 问题 | 级别 | 状态 |
|---|---|---|
| R-M4-08 balance 口径 | P1 | ✅ 关闭 |
| R-M4-09 叙述缓存 | P1 | ✅ 关闭 |
| R-M4-10 漏记 tokens | P2 | ✅ 关闭 |
| R-M4-11 parity 守卫 | P2 | ✅ 关闭 |
| R-M4-12 哈希覆盖 | P3 | ✅ 关闭 |
| R-M4-13 文档失真 | P3 | ✅ 关闭（测试数 17+20=37 已如实反映） |
| R-M4-14 "本月"措辞 | P3 | ✅ 关闭 |
| R-M4-15 限流降级 | P3 | ✅ 关闭 |
| R-M4-17 HEAD 自洽 | P1 | ✅ 关闭 |
| R-M4-38 客户端 balance clamp | P1 | ✅ 关闭 |

**新增 2 项 P4 观察（NEW-1/NEW-2），均不阻塞。**

**T-M4-03 整体通过，可交付。** 建议承接 T-M4-03c 后的后续工作（监控指标、Redis 缓存替换等），或继续推进其他任务线。
