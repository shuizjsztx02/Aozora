# T-M4-03 重新审查报告（第 7 轮）

- **审查日期：** 2026-09-07
- **审查范围：** T-M4-03a（事实包服务）+ T-M4-03b（叙述生成与数字校验）+ T-M4-03c（客户端洞察展示）
- **基线：** HEAD `b4e0c39` + 工作树文档改动
- **模式：** 全量代码审查 + 实测验证
- **上一轮：** 第 6 轮（2026-09-06，R-M4-38 修复核验 + 文档数字刷新）

---

## 一、实测结果（第 7 轮实际运行）

| 检查项 | 命令 | 结果 |
|---|---|---|
| 任务级服务端测试 | `pytest tests/test_fact_pack.py tests/test_narrative_report.py -q` | **37 passed** ✅ |
| 全量服务端测试 | `pytest tests/ -q --tb=no` | **424 passed, 2 skipped** ✅（无 error） |
| 客户端全量测试 | `npx vitest run` | **452 passed**（26 test files）✅ |
| HEAD 自洽性 | 工作树无未提交的代码改动（仅文档） | ✅ 纯净 |

**较第 6 轮变化：** 全量服务端测试从 406→424 passed（+18），error 从 1→0（Windows 环境问题也已消失）。客户端从 426→452 passed（+26，T-M4-03c 与其他任务的测试增长）。

---

## 二、第 6 轮问题状态核验

| 问题 | 第 6 轮状态 | 第 7 轮核验 |
|---|---|---|
| R-M4-08 (P1) balance 口径 | ✅ 已关闭 | ✅ **确认保持关闭** — `statistics/service.py:156,226` 均 `balance=total_income - total_expense`（无 clamp）；`statistics/schemas.py:27,38` balance 为 `int`（无 `ge=0`）；`fact_pack.py:215` 同口径；`aggregator.ts:50-52` 客户端同口径。4 个 parity 测试锁死。 |
| R-M4-09 (P1) 叙述缓存 | ✅ 已关闭 | ✅ **确认保持关闭** — `service.py:67-71` 缓存定义，`service.py:822-839` 缓存命中路径（不 LLM、不计费、`narrative_cached=True`），`service.py:979-981` 缓存存储。2 个 mock provider 测试。 |
| R-M4-10 (P2) 漏记 tokens | ✅ 已关闭 | ✅ **确认保持关闭** — `service.py:932-936` invalid_json 耗尽路径补记；`service.py:945-949` 成功路径在 verification 前即记录。 |
| R-M4-11 (P2) parity 守卫 | ✅ 已关闭 | ✅ **确认保持关闭** — `test_fact_pack.py:384-` `TestStatisticsParity` 含 4 个 HTTP 级测试（monthly totals、deficit balance、trend、category breakdown）。 |
| R-M4-12 (P3) 哈希覆盖 | ✅ 已关闭 | ✅ **确认保持关闭** — `fact_pack.py:132-175` `_compute_data_hash` 覆盖 tx_count、category_name、largest tx id/note/biz_date、trend transaction_count。 |
| R-M4-13 (P3) 文档失真 | ✅ 已关闭 | ⚠️ **微瑕**（见 §三-1） |
| R-M4-14 (P3) "本月"措辞 | ✅ 已关闭 | ✅ **确认保持关闭** — `prompts.py:69-72` 四处均用"当月"；`service.py:890` 用"{period}月度"。 |
| R-M4-15 (P3) 限流降级 | ✅ 已关闭 | ✅ **确认保持关闭** — `service.py:869-883` 限流命中 → INFO 日志 + return（narrative=null, 200）。 |
| R-M4-17 (P1) HEAD 自洽 | ✅ 已关闭 | ✅ **确认保持关闭** — 全量 424 passed, 0 error。 |
| R-M4-38 (P1) 客户端 balance clamp | ✅ 已关闭 | ✅ **确认保持关闭** — `aggregator.ts:50-52` 返回 `income - expense`（有符号 `number`）；`money.ts:111` `formatSigned` 支持负值显示。 |

---

## 三、本轮新发现

### 1. 🟢 文档微瑕 — `T-M4-03-final-summary.md` 测试数仍滞后

- **级别：** P3（文档）
- **文件：** `T-M4-03-final-summary.md:21-27`
- **现状：** 文件写"13 个测试"（T-M4-03a）和"15 个测试"（T-M4-03b），实际为 **20 + 17 = 37**（`pytest tests/test_fact_pack.py tests/test_narrative_report.py -v` 可验）。
- **影响：** 不影响功能，但与"文档如实反映实现"的原则不符。
- **建议：** 提交前刷新。

### 2. 代码质量观察（非问题，记录备查）

以下为审查过程中的代码质量观察，均**不构成阻塞问题**：

| 观察 | 位置 | 评估 |
|---|---|---|
| 缓存淘汰为 O(n) 扫描 | `service.py:122`、`fact_pack.py:117` | max 500 条目下 <1ms，无需优化。若未来扩大规模可改为 `OrderedDict`。 |
| `_find_undeclared_numbers` 将 lookup 全部值视为已声明 | `narrative.py:191-201` | 已文档化（`narrative.py:179-189` docstring），有意设计。作为二级安全网足够。 |
| `trend_direction` 10% 阈值为隐式硬编码 | `narrative.py:122-127` | 产品决策，当前合理。如需调整应提取为常量。 |
| Hash 截断为 16 hex（64 bit） | `fact_pack.py:175` | 500 条目缓存下碰撞概率 <10⁻¹²，安全。 |

---

## 四、安全审计（第 7 轮，与历史轮次一致）

| 检查项 | 结果 |
|---|---|
| LLM 数据隔离 | ✅ LLM 只看到 `build_context_dict` 输出的事实包，不接触原始 DB |
| 数字溯源验证 | ✅ `data_references` 逐一比对，不匹配 → 丢弃叙述 + WARNING 日志 |
| 未声明数字检测 | ✅ `_find_undeclared_numbers` 扫描 narrative 中 ≥100 的整数 |
| 校验失败降级 | ✅ 丢弃叙述，返回纯事实包 200，不暴露内部错误 |
| 所有权校验 | ✅ `FactPackService.build()` 验证 `Book.owner_id == user_id` |
| 速率限制 | ✅ `_check_rate_limit` + `_check_daily_quota` 保护叙述生成路径 |
| 限流降级 | ✅ 限流命中 → 200 + narrative=null（R-M4-15 修复） |
| SQL 注入 | ✅ 全部 SQLAlchemy ORM 参数化查询 |
| 安全事件日志 | ✅ 校验失败记 WARNING + `number_verification_failed` 标签 |
| Token 审计 | ✅ 所有 provider 调用路径均记录 AIUsage（含重试/校验失败路径） |
| Prompt 注入 | ✅ 用户输入仅通过 `user_input` 传入，系统提示独立且明确约束 |
| 客户端 balance 口径 | ✅ 与服务端一致（income - expense，允许负值） |

---

## 五、门禁结论（第 7 轮）

**实现层（服务端 T-M4-03a/03b）：✅ 通过。**

- 37 个任务级测试全绿
- 全量 424 passed / 2 skipped / 0 error
- 安全链路完整：事实包隔离 → data_references 校验 → 未声明数字检测 → 降级不暴露内部错误
- 对账不变量有 4 个 HTTP 级 parity 测试守卫
- Token 审计在所有路径上完整（含重试/校验失败/限流降级）

**跨端契约：✅ 通过。**

- 客户端 `computeBalance` 与服务端 `/stats/monthly` + `/ai/report` 口径一致（income - expense，允许负值）
- `MonthlyStats.balance` 类型从 `MoneyCents` 改为有符号 `number`
- `formatSigned` 正确显示赤字（`-¥12.50`）
- 4 个 parity 测试锁死跨端一致性

**客户端（T-M4-03c）：✅ 通过。**

- AiInsightCard 10 测试全绿
- 事实包摘要 + AI 叙述展示
- localStorage 缓存 1h TTL（键 `bookId:period`）
- `navigator.onLine` 离线降级（不请求、不渲染卡片）
- `narrative_cached` 标记展示

**交付层：✅ 通过（待原子提交）。**

- HEAD 测试全绿（424 passed / 2 skipped / 0 error）
- 工作树仅有文档改动（`T-M4-03-final-summary.md` 等），无未提交代码
- R-M4-17 HEAD 自洽已关闭

**唯一残留：** `T-M4-03-final-summary.md` 测试数 13/15 应为 20/17（P3 文档微瑕，不阻塞）。

---

## 六、历史问题全量状态表（第 7 轮终态）

| 问题 | 级别 | 最终状态 | 关闭轮次 |
|---|---|---|---|
| R-M4-08 balance 口径 | P1 | ✅ 关闭 | 第 6 轮 |
| R-M4-09 叙述缓存 | P1 | ✅ 关闭 | 第 3 轮 |
| R-M4-10 漏记 tokens | P2 | ✅ 关闭 | 第 5 轮 |
| R-M4-11 parity 守卫 | P2 | ✅ 关闭 | 第 4 轮 |
| R-M4-12 哈希覆盖 | P3 | ✅ 关闭 | 第 5 轮 |
| R-M4-13 文档失真 | P3 | ⚠️ 微瑕 | 测试数仍滞后（P3） |
| R-M4-14 "本月"措辞 | P3 | ✅ 关闭 | 第 5 轮 |
| R-M4-15 限流降级 | P3 | ✅ 关闭 | 第 4 轮 |
| R-M4-17 HEAD 自洽 | P1 | ✅ 关闭 | 第 4 轮 |
| R-M4-38 客户端 balance clamp | P1 | ✅ 关闭 | 第 6 轮 |

**10 项中 9 项完全关闭，1 项 P3 文档微瑕待刷新。T-M4-03 整体通过。**
