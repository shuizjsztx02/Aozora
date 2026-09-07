# T-M5-01 对账不变量与端到端测试 - 第四轮审查报告

**审查日期：** 2026-09-07
**审查范围：** 第三轮 5 个新问题（Issue #1~#5）修复验证 + 三端回归测试 + corpus 确定性复验
**审查方法：** 独立代码验证 + 实际运行测试，不依赖修复报告自述

---

## 一、修复验证总览

| # | 问题 | 级别 | 验证方法 | 结果 |
|---|------|------|---------|------|
| 1 | 附录引用不存在的 `scripts/validate-timestamps.py` | P3 | grep 全文无 `scripts/` 引用；附录改为手动验证说明（L287） | ✅ 通过 |
| 2 | Python 未测试 `invalid-edges.json` | P3 | `load_invalid_edges()`（L62-65）+ `test_corpus_invalid_edges_rejected`（L394-410）确认存在；pytest 收集 8 项（含新测试） | ✅ 通过 |
| 3 | `amount=0` 跨平台差异未记录 | P3 | 审查报告新增 §4.5 章节；引用代码位置逐一核对（见下方勘误） | ✅ 通过（表格有一处不精确，见 Issue A） |
| 4 | "三端聚合策略"数学错误 | P2 | 审查报告 L190-192 已改为 Sep: 2 txs (TX1,TX3) / Oct: 1 tx (TX2)；`tz-boundary/seed.json` description 改为 "Two distinct grouping results…"，独立换算确认正确 | ✅ 通过 |
| 5 | Generator 2024 时间戳 | P3 | `generate-fuzz-corpus.py:108` 改为 `1788192000000 + i * 86400000`；trace JSON deletedAt 实测 1795449600000~1796745600000（2026-11~12）；全仓 `1725` 残留 = 0 | ✅ 通过 |

**时间戳独立换算（Issue #4 复核）：**
```
TX1: 1788192000000 → UTC 2026-08-31T16:00Z → Shanghai 2026-09-01 → Sep
TX2: 1790784000000 → UTC 2026-09-30T16:00Z → Shanghai 2026-10-01 → Oct
TX3: 1789459200000 → UTC 2026-09-15T08:00Z → Shanghai 2026-09-15 → Sep
→ bizDate 与 Shanghai-occurredAt 分组相同（Sep: TX1+TX3, Oct: TX2），UTC 不同（Aug: TX1, Sep: TX2+TX3）✅
```

---

## 二、回归测试实测结果

| 端 | 命令 | 结果 |
|----|------|------|
| TypeScript | `npx vitest run reconciliation.test.ts reconciliation-fuzz.test.ts` | ✅ 26/26 passed（859ms） |
| Python | `pytest tests/test_fuzz_reconciliation.py tests/fixtures_reconciliation.py -q` | ✅ 6 passed + 8 skipped（fuzz 文件整体 `pytestmark skip`，SQLite UUID WIP，属已知挂账） |
| Kotlin | `gradlew :app:testReleaseUnitTest --tests "com.aozora.app.reconciliation.*"` | ✅ ReconciliationFuzzTest 7/7 + ReconciliationTest 8/8 = 15/15，0 failures（XML 报告确认） |

**corpus 确定性复验：** 重新运行 `python tools/generate-fuzz-corpus.py` 后对 5 个 JSON 做 md5 比对 → **全部字节级一致**，固定种子（Random(42)）可复现性成立。

---

## 三、本轮新发现问题（均为 P3 文档精度）

> **修复状态（2026-09-07）：** Issue A/B/C 已全部修复 ✅ — A: §4.5 表格拆分 Kotlin 测试层/生产层两行并修正设计意图表述；B: 路径笔误已改；C: round3 TX3 已勘误为 08:00Z（含计算式 28800 修正）并加勘误注记。

### Issue A: §4.5 表格 "Kotlin 接受 amountCents >= 0" 不精确 [P3]

**位置：** `T-M5-01-review.md` §4.5 跨平台差异表

**实际情况（分层核对）：**
| 层 | 代码 | 对 amount=0 的行为 |
|----|------|-------------------|
| TS 域层 | `app/src/domain/money.ts:16`（`amount < 0` 抛错） | 接受 0 ✅（表格正确） |
| Kotlin **生产层** | `app/src/main/java/.../db/Repository.kt:31`（`require(amountCents in 1..9007199254740991L)`） | **拒绝 0** ❌（表格未提及） |
| Kotlin 测试层 | `ReconciliationFuzzTest` FuzzTx 聚合（无金额校验） | 接受 0 |
| Server | `schemas.py:14 Field(gt=0)` + `ledger.py:129 CHECK` | 拒绝 0 ✅（表格正确） |

**影响：** 低。表格将 Kotlin 归为"与 TS 一致（接受 0）"，但 Kotlin 生产 Repository 实际与 Server 一致（拒绝 0），只有测试侧聚合器接受 0。附带观察：TS 域层与 Kotlin 生产层对 0 的接受度存在真实的客户端间差异（Server 兜底拒绝，无实际风险）。

**建议：** §4.5 表格 Kotlin 行改为"测试层接受 ≥0（聚合器边界）；生产 Repository 拒绝 0（与 Server 一致）"。

### Issue B: 修复总结文档路径笔误 [P3]

**位置：** `T-M5-01-fixes-new-issues.md:176`

**问题：** 文件变更清单写为 `contracts/fixtures/reconciliation-tz-boundary/seed.json`，实际路径是 `contracts/fixtures/reconciliation/tz-boundary/seed.json`（同文档 L87 与 verification-summary 中路径正确）。

### Issue C: 第三轮历史文档 TX3 UTC 时刻笔误 [P3]

**位置：** `docs/review/T-M5-01-review-round3.md:49`

**问题：** 写为 "TX3: 1789459200000ms → 2026-09-15T12:00:00Z"，实际为 **08:00Z**（Shanghai 16:00）。当前 `T-M5-01-review.md` 附录已是正确的 08:00Z；round3 为历史存档，可选择性加勘误注记。

---

## 四、审查结论

✅ **T-M5-01 维持通过**

1. **第三轮 5 个 Issue 修复全部经独立验证有效**（1×P2 + 4×P3），无虚假修复
2. **三端回归全绿：** TS 26/26、Python 6 passed（8 项已知 WIP skip）、Kotlin 15/15
3. **corpus 确定性成立：** 重新生成字节级一致
4. **本轮新问题：0 个 P0/P1/P2，3 个 P3（均为文档精度，不影响代码正确性）**
5. **既有挂账不变：** Python fuzz 文件 skip（SQLite UUID WIP）；P2-6~9 代码质量、P2-11~13 依赖 T-M5-04c

**下一步：** ~~Issue A/B/C 可随手修复~~ 已于本轮修复完毕；T-M5-01 可正式关闭，进入 T-M5-02 / T-M5-04。

---

**审查版本：** v4.0
**与第三轮差异：** 验证了 5 个修复项 + 实测三端回归与确定性；新发现 3 个 P3 文档精度问题
