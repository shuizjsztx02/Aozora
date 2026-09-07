# T-M4 重新审查报告

**审查日期：** 2026-09-07
**审查范围：** T-M4 全量（T-M4-00 至 T-M4-06，含所有字母后缀子任务）
**审查基线：** HEAD `a35ac37` + 工作树（91 文件未提交：50 修改 + 41 未跟踪）
**审查方法：** 实测验证（pytest / vitest / tsc / gradle）+ 源码抽查 + 文档一致性核对

---

## 一、总体结论

**T-M4 代码本身质量已达标**——01/03/04/05/06 经多轮审查（最高至 round8）已确认通过，T-M4-02 已提交。但**工作树混入了 T-M5 早期开发代码**，导致以下阻塞：

1. 🚨 **Kotlin 编译失败**：未跟踪的 `export/ExportClient.kt` 类型推断错误，整个 `:app:compileDebugKotlin` 无法通过
2. 🚨 **Python 测试失败**：未跟踪的 `test_e2e_journey.py::test_trends_endpoint` 与 schema 不一致（YYYY-MM-DD vs YYYY-MM）
3. 🟡 **工作树污染**：T-M4-04 round6 修复与 T-M5 早期代码（export/backup/consent/legal）混在一起，无法独立验证

**建议行动：** 先拆分提交（T-M4-04 round6 修复 → 单独 commit），再修复两个编译/测试 bug，最后更新文档状态行。完成上述后 T-M4 可整体标记交付。

---

## 二、实测基线（HEAD `a35ac37` + 工作树）

| 检查项 | 命令 | 结果 |
|---|---|---|
| Python 服务端全量 | `pytest server/tests/ -q` | **514 passed / 1 failed / 2 skipped** ❌ |
| 失败项 | `test_e2e_journey.py::TestTrendsE2E::test_trends_endpoint` | 422 — schema 期望 `YYYY-MM`，测试发送 `YYYY-MM-DD` |
| TypeScript 客户端 | `npx vitest run` | **545 passed / 33 files** ✅ |
| TypeScript typecheck | `npx tsc --noEmit` | **零错误** ✅ |
| Kotlin 单测 | `./gradlew :app:testDebugUnitTest` | **BUILD FAILED** ❌（ExportClient.kt 编译错误） |
| T-M4-01/03 任务级测试 | `pytest tests/test_ai_gateway.py test_ai_proposal_*.py test_fact_pack.py test_narrative_report.py test_auth.py test_config.py` | **178 passed** ✅ |
| T-M4-04c 任务级测试 | `pytest tests/test_attachments.py` | **31 passed** ✅ |

---

## 三、关键 P1 修复复验

### 3.1 T-M4-01 P1-1 Token 审计一致性 ✅ 已修复

**文件：** `server/src/modules/ai/service.py`

**证据：**
- `_record_failure_usage` 方法定义在 L403
- 在 `interpret()` L203/L226、`_generate_narrative()` L997/L1016、`parse_image()` L1182/L1202 共 **6 处** retry 失败路径调用
- `test_ai_gateway.py` 54 passed，含 `test_invalid_json_failure_records_tokens` / `test_provider_error_failure_failure_records_prior_tokens` 等回归用例

**结论：** 修复属实，token 审计覆盖所有失败路径（成功/重试/校验失败/invalid_json 耗尽）。

### 3.2 T-M4-01 P1-2 `parse_image` 独立测试 ✅ 已补齐

**证据：** `TestParseImage` 类存在于 `test_ai_gateway.py`，覆盖 happy path / 410 expired / 409 not pending 三个核心路径。

### 3.3 T-M4-04 P1 stopRecording 资源泄漏 ✅ 已修复

**文件：** `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt`

**证据：**
- L262-273：`synchronized(stateLock)` 内捕获本地引用 + 字段置 null
- L302：`job?.cancelAndJoin()` 等待 recognitionLoop 退出
- L307-308：`try { record?.release() } catch (_: Exception) { }` + `try { rec?.close() } catch (_: Exception) { }`
- L299-301 注释修正：明确说明"finally 不释放，必须由本处显式释放"

**结论：** 修复属实，符合 round6 审查建议。每次 start→stop 周期都会走到 L307-308，AudioRecord + Recognizer 不再泄漏。

### 3.4 T-M4-03 / T-M4-05 / T-M4-06 ✅ 历史审查属实

- T-M4-03 round8：37 任务级测试 + 426 全量 passed，4 个 HTTP 级 parity 守卫（`TestStatisticsParity`）
- T-M4-05 round5：R-M4-41~46 修复属实（Vosk 模型释放、外部 scope 绑定、TOCTOU、权限防御）
- T-M4-06 4 轮审查：R-M4-12/13/17/18/19 修复属实，`FontScaleAdapter` 替代死代码 `useFontScale`

---

## 四、新发现的问题

### 4.1 🚨 阻塞 — `test_e2e_journey.py` 与 schema 不一致

**文件：** `server/tests/test_e2e_journey.py:278-282`（**未跟踪**）

```python
def test_trends_endpoint(self, e2e_client, shared_data):
    period_from = TODAY.replace(day=1).isoformat()  # → "2026-09-01"
    period_to = TODAY.isoformat()  # → "2026-09-07"
    r = e2e_client.get(f"/stats/trends?book_id=...&period_from={period_from}&period_to={period_to}")
    assert r.status_code == 200
```

**问题：** `period_from/period_to` 发送 `YYYY-MM-DD`，但 `server/src/modules/statistics/routes.py:35-36` 的 schema 正则 `^\d{4}-(0[1-9]|1[0-2])$` 期望 `YYYY-MM`。

**对比：** `test_fact_pack.py:504-508` 正确发送 `"2026-04" / "2026-09"`（YYYY-MM），测试通过。

**修复：** 将 `TODAY.replace(day=1).isoformat()` 改为 `TODAY.strftime("%Y-%m")` 或 `TODAY.replace(day=1).strftime("%Y-%m")`。

### 4.2 🚨 阻塞 — `ExportClient.kt` 编译失败

**文件：** `app/src/main/java/com/aozora/app/export/ExportClient.kt:183, 187, 194`（**未跟踪**）

**错误：** `Cannot infer type for this parameter. Please specify it explicitly.`

**根因：** `suspendSend` 函数（L199-212）返回类型靠表达式推断，调用处 `val response = suspendSend(request)` 推断失败。

**修复：** 给 `suspendSend` 显式声明返回类型：
```kotlin
private suspend fun suspendSend(request: Request): okhttp3.Response = suspendCoroutine<okhttp3.Response> { ... }
```

**归属判断：** `export/` 模块是 T-M5-04b（用户数据导出）的早期开发，**不属于 T-M4 范围**。但它在工作树中，导致整个 Kotlin 编译失败，阻塞了 T-M4 的编译验证。

### 4.3 🟡 中优先级 — 工作树污染

**工作树组成分析：**

| 类别 | 文件示例 | 数量 | 归属 |
|---|---|---|---|
| T-M4-04 round6 修复 | `VoiceRecognitionService.kt`, `PermissionManager.kt`, `QuickEntrySheet.test.tsx` | ~20 | T-M4 |
| T-M5-04b 导出功能 | `export/ExportClient.kt`, `ExportPage.tsx`, `ExportPage.test.tsx` | ~15 | T-M5 |
| T-M5-03 备份 | `ADR-005-backup-strategy.md`, `docker-compose.backup.yml` | ~5 | T-M5 |
| T-M5-04c 用户同意 | `consent/UserConsentEntity.kt`, `UserConsentDao.kt` | ~5 | T-M5 |
| T-M5-02a 安全 | `security/`, `reconciliation/` 测试 | ~10 | T-M5 |
| T-M5-02c 密钥扫描 | `secret-scan.yml` | 1 | T-M5 |
| 其他新文件 | `debug/`, `release/`, `legal/`, `fixtures/` | ~15 | T-M5+ |
| E2E 测试（有 bug） | `test_e2e_journey.py` | 1 | T-M5? |

**问题：** T-M4-04 round6 修复与 T-M5 早期代码混在同一个工作树，无法独立验证 T-M4。

**建议：** 拆分为至少两个 commit：
1. `fix(T-M4-04): round6 审查修复`（T-M4 相关 ~20 文件）
2. `feat(T-M5): early development of export/backup/consent/security`（T-M5 早期）

---

## 五、文档一致性核查

### 5.1 🟡 `production-execution-plan.md` 顶层状态行失真

**第 3 行（当前）：**
> T-M4-04 语音、图片与桥接 ✅（2026-09-07 **第 5 轮**复审通过：04a/04c/04d/04e 全部通过...）

**应更正为：**
> T-M4-04 语音、图片与桥接 ✅（2026-09-07 **第 6 轮**复审通过：04a/04c/04d/04e 全部通过，**第 6 轮新发现 P1 stopRecording 资源泄漏已修复**）

**影响：** 历史追溯时无法反映第 6 轮的 P1 修复。

### 5.2 ✅ 子任务表已更新

- 第 624-626 行：T-M4-04c/d/e 已标注"第 6 轮 2026-09-07"，属实
- 第 571-576 行：T-M4-00/01/03/04/05/06 状态标记与审查结论一致

### 5.3 ✅ T-M4-01/02/03 closure 报告

- `T-M4-01-closure.md` 测试数字已订正（R-M4-02）
- `T-M4-02-completion-summary.md` 已完成，待推送说明已过时（c101afb 已 push）
- `T-M4-03-final-summary.md` API 示例中叙述仍用"本月"（R-M4-NEW-1，P4 不阻塞）

---

## 六、T-M4 各子任务终态

| 子任务 | 状态 | 备注 |
|---|---|---|
| T-M4-00 前端骨架 | ✅ 完成 | 7 个子任务全绿 |
| T-M4-01 AI Gateway | ✅ 完成 | round6 通过，P1 token 审计修复属实，54 AI gateway 测试通过 |
| T-M4-02 动作协议 | ✅ 完成 | 已提交 c101afb，17 文件变更，15855 行插入 |
| T-M4-03 AI 查询与洞察 | ✅ 完成 | round8 通过，37 任务级测试 + 4 parity 守卫 |
| T-M4-04a 权限状态机 | ✅ 完成 | Robolectric + TS 测试全绿 |
| T-M4-04b Vosk 语音 | ⏳ 代码完成，待真机 | P1 资源泄漏已修，但无 instrumentation 测试守卫（round6 建议） |
| T-M4-04c 附件 API | ✅ 完成 | round6 通过，31 测试 |
| T-M4-04d 客户端图片 | ✅ 完成 | round6 通过 |
| T-M4-04e 图片解析 | ✅ 完成 | round6 通过 |
| T-M4-05 通知/快捷方式/小组件 | ✅ 完成 | round5 通过，真机复验待执行 |
| T-M4-06a 视觉打磨 | ✅ 完成 | 13 对比度测试通过 |
| T-M4-06b 无障碍 | ✅ 完成 | 低性能模式 + 字体适配 |
| T-M4-06c 截图基线 | ✅ 完成 | Playwright 配置就绪，CI 门禁待 T-M5-01d 整合 |

---

## 七、行动项（按优先级）

### 🔴 P0 — 阻塞 T-M4 整体交付

1. **修复 `test_e2e_journey.py` schema 不一致**：`period_from/period_to` 改为 `YYYY-MM` 格式
2. **修复 `ExportClient.kt` 编译错误**：给 `suspendSend` 显式返回类型，或临时将 `export/` 目录排除出编译
3. **拆分工作树提交**：
   - Commit A：`fix(T-M4-04): round6 审查修复`（仅 T-M4 相关文件）
   - Commit B：`feat(T-M5 early): export/backup/consent/security scaffolding`（T-M5 早期代码，标记为 WIP）

### 🟡 P1 — 文档与治理

4. **更新 production-execution-plan.md 顶层状态行**：反映 T-M4-04 第 6 轮（含 P1 修复）
5. **T-M4-04b 补 instrumentation 测试**：`start→stop→start→stop` 验证 AudioRecord 释放（round6 建议，防回归）

### 🟢 P2 — 后续迭代

6. **T-M4-04b 真机复验**：按 `T-M4-05-device-verification-plan.md` 执行（含 voice/image 全链路）
7. **T-M4-05 真机复验**：同上
8. **T-M4-03 NEW-1/NEW-2**（P4）：文档示例"本月"→"当月"；AiInsightCard 改用 `reportAI` 辅助方法

---

## 八、审查产物

- 报告文件：`docs/review/T-M4-review-round1-overall.md`（本文件）
- 实测基线：HEAD `a35ac37` + 工作树（91 文件未提交）
- 测试数字：Python 514/1/2、TypeScript 545/33、Kotlin BUILD FAILED
- 审查人：Claude Code
- 审查耗时：~25 分钟
