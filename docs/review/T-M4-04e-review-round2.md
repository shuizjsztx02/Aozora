# T-M4-04e 代码审查（第 2 轮）

**审查日期：** 2026-09-07
**审查范围：** 同第 1 轮（T-M4-04e 图片记账解析与批量确认全栈）
**审查目的：** 复验第 1 轮 P1/P2 修复，扫描新引入问题

---

## 审查结论

**✅ 通过（附 P3 建议）。** 第 1 轮 4 个 P1 全部修复，5 个 P2 全部修复或可接受。新发现 2 个 P3，无新 P1/P2。

端到端链路（图片上传 → AI 解析 → 批量确认 → execute → 附件链接）可正常运行。

---

## 第 1 轮问题复验

### P1 复验（全部 ✅）

| ID | 问题 | 状态 | 修复位置 |
|----|------|------|----------|
| R-M4-51 | `meta` 字段被 schema 拒绝 | ✅ 已修复 | `schema.json` L45-48 新增 `meta` 属性定义（`type: object`） |
| R-M4-52 | 空 `bizDate` 导致 execute 失败 | ✅ 已修复 | `service.py` L1213-1216：空 `bizDate` 回退 `today_str` |
| R-M4-53 | 未识别分类 `categoryId: ""` 被拒 | ✅ 已修复 | `service.py` L1209-1211：回退 `fallback_category_id` |
| R-M4-54 | 零交易解析结果导致 UI 死区 | ✅ 已修复 | `QuickEntrySheet.tsx` L96-108：零交易设为 error 状态，proposal 置 null |

### P2 复验（全部 ✅）

| ID | 问题 | 状态 | 修复位置 |
|----|------|------|----------|
| R-M4-55 | `book.list` 重复调用 3+ 次 | ✅ 已修复 | `QuickEntrySheet.tsx` L50-58：`bookIdRef` 缓存，挂载周期内只调用一次 |
| R-M4-56 | 客户端不限制 parse 请求频率 | ✅ 可接受 | `parsedAttachmentRef`（L82）防止同 attachment 重复；服务端 rate limit 兜底 |
| R-M4-57 | 零交易 proposal 仍写入草稿 | ✅ 已修复 | `QuickEntrySheet.tsx` L100-107：零交易时 `proposal: null`，不写 proposal 到草稿 |
| R-M4-58 | `handleBatchConfirm` 跳过 validate | ✅ 已修复 | `QuickEntrySheet.tsx` L233-238：execute 前先调 `bridge.validateProposal()` |
| R-M4-59 | `executeProposal` 后未清图片状态 | ✅ 已修复 | `QuickEntrySheet.tsx` L248：`await image.clear()` 后关闭 sheet |

---

## 测试验证情况

### 服务端 `test_image_parse.py`（17 用例）

| 场景 | 覆盖 |
|------|------|
| 单笔记账解析（含分类解析 + 日期） | `test_single_transaction_parse` ✅ |
| 多笔批量（含模糊分类 + 空 bizDate 回退） | `test_multi_transaction_batch` ✅ 显式验证 R-M4-52/53 修复 |
| 精确匹配优先于前缀匹配 | `test_category_prefix_match` ✅ |
| 空图片零交易 | `test_empty_image_no_transactions` ✅ |
| 跨用户附件隔离 404 | `test_cross_user_attachment_404` ✅ |
| 过期附件 410 | `test_expired_attachment_410` ✅ |
| 已解析附件 409 | `test_already_parsed_409` ✅ |
| 已链接附件 409 | `test_linked_attachment_409` ✅ |
| AI 服务不可用 503 | `test_no_provider_503` ✅ |
| Provider 错误重试后失败 | `test_provider_error_retry_then_fail` ✅ |
| 非法 JSON 重试后失败 | `test_invalid_json_retry_then_fail` ✅ |
| 超时不重试 | `test_timeout_not_retried` ✅ |
| 非法 book_id 422 | `test_invalid_book_id_422` ✅ |
| 文件缺失（磁盘） | `test_file_missing_on_disk` ✅ |
| Rate limit 429 | `test_rate_limit_429` ✅ |
| Vision 模型消息格式 | `test_vision_model_called_with_image` ✅ |

**覆盖充分。** `test_multi_transaction_batch` 显式验证：
- R-M4-52：`txs[2]["bizDate"] != ""` 且长度为 10（YYYY-MM-DD）
- R-M4-53：`txs[2]["categoryId"] != ""`（未识别"购物"回退到第一个分类）

---

## 新发现问题

### P3 — 低优先级（本次不修复）

#### R-M4-64：`fallback_category_id` 在零分类用户场景下仍为 `""`

**文件：** `server/src/modules/ai/service.py:1191`
**说明：**

```python
fallback_category_id = next(iter(ctx.category_names.values()), "")
```

若用户从未创建任何分类（`ctx.category_names` 为空 dict），`fallback_category_id` 为 `""`。此时 R-M4-53 修复失效：未识别分类的 `categoryId` 仍为 `""`，`schema.json` `minLength: 1` 仍会拒绝。

**影响：** 仅当用户零分类时触发（正常 onboarding 流程通常已有默认分类），非主流路径。无对应用例覆盖（`test_image_parse.py` 中所有测试均 seed 了分类）。

**建议：** 在 `parse_image` 入口提前检查 `ctx.category_names` 是否为空，若空则返回明确错误码（如 `no_categories`），客户端引导用户先创建分类。

---

#### R-M4-65：`handleBatchConfirm` 校验失败时无用户反馈

**文件：** `app/src/features/ledger/QuickEntrySheet.tsx:234-237`
**说明：**

```typescript
if (!validation.valid) {
  console.warn('Proposal validation failed:', validation.errors)
  setBatchSubmitting(false)
  return
}
```

校验失败时只 `console.warn`，用户无任何感知——按钮停止旋转，但无错误提示，不知发生了什么。

**建议：** 校验失败时设 `parseState('error')` 并给出错误信息，或将 `validation.errors` 展示在 BatchConfirmationCard 上。

---

## 仍存在的历史 P3（不变）

| ID | 问题 | 状态 |
|----|------|------|
| R-M4-60 | `BatchConfirmationCardProps.attachmentId` 未使用（死代码） | 仍存在 |
| R-M4-61 | 服务端全量读文件 + base64 膨胀（当前 4MB 上限可控） | 仍存在，已知 |
| R-M4-62 | 分类前缀匹配过于宽松（精确匹配优先，有 fallback 兜底） | 仍存在，风险可控 |
| R-M4-63 | 客户端测试缺口：`image-draft-store.test.ts` 无 `parseError`/`proposal` 测试，无 `BatchConfirmationCard` 组件测试 | 仍存在 |

---

## 确认无问题的修复细节（值得记录）

| 修复点 | 验证细节 |
|--------|----------|
| R-M4-51 schema 新增 `meta` | `meta` 定义为 `type: object`，无 `additionalProperties: false`，可接受任意键值；TS `ActionProposal` 接口（`action-proposal.ts` L58）同步新增 `meta?: Record<string, unknown>` ✅ |
| R-M4-52 bizDate 回退 | `today_str` 使用 `datetime.now(tz=UTC).date().isoformat()`（L1193），确保跨时区一致；测试 `test_multi_transaction_batch` 显式断言 `len == 10` ✅ |
| R-M4-53 分类 fallback | 精确匹配优先于前缀匹配（L1200 vs L1203-1207）；`test_category_prefix_match` 验证此顺序 ✅ |
| R-M4-54 零交易 UI | `parseProposal` 被设为 null → `showBatchCard` 条件 L323 为 false → 走普通表单分支；error UI（L516）显示"未识别到消费记录" ✅ |
| R-M4-57 草稿防死区 | 零交易时 `saveDraft({ proposal: null, parseError: 'no_transactions' })`；恢复逻辑（L65-76）中 `draft?.proposal` 为 null，不进入 done 分支，正确显示 error UI ✅ |
| R-M4-58 客户端预校验 | `bridge.validateProposal` 调 `/ai/validate`，服务端走 `validate_proposal`（含 schema + context hallucination 检查）；客户端提前拦截 ✅ |
| R-M4-59 状态清理 | execute 成功后：`image.clear()` → `clearDraft()` → `parsedAttachmentRef.current = null` → `onSuccess()`；顺序正确，sheet 关闭 ✅ |

---

## 修复优先级总结

| 级别 | 问题 | 建议 |
|------|------|------|
| P1（阻塞） | 无 | — |
| P2（重要） | 无 | — |
| P3（本次不修） | R-M4-64（零分类 fallback）| 后续迭代处理 |
| P3 | R-M4-65（validate 失败无反馈）| 后续迭代处理 |
| P3 | R-M4-60/61/62/63（历史遗留） | 按原计划跟进 |

---

## 复验建议（端到端场景）

修复上线前建议手动走一遍：

1. ✅ 正常路径：拍照 → 解析 → N 笔交易 → 编辑 → 确认 → execute → sheet 关闭
2. ✅ 无日期路径：AI 返回空 bizDate → 回退当天 → 用户可见可编辑 → 确认成功
3. ✅ 零交易路径：AI 无法识别 → 显示错误 UI（"未识别到消费记录"）→ 可重试/手动记账
4. ⚠️ 零分类用户路径：尚未覆盖（R-M4-64），建议补充 `parse_image` 入口检查后再上生产
