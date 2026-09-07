# T-M4-04e 代码审查（第 1 轮）

**审查日期：** 2026-09-07
**审查范围：** T-M4-04e 图片记账解析与批量确认（全栈）
**审查依据：** `production-execution-plan.md` T-M4-04e 交接产物定义 + `contracts/action-proposal/schema.json` + `APP-CONTEXT.md §6.3/§6.4`

---

## 审查结论

**⚠️ 不通过。** 发现 4 个 P1（阻塞级）+ 5 个 P2 + 4 个 P3。

端到端链路（图片上传 → AI 解析 → 批量确认 → 执行 → 附件链接）存在 schema 校验不兼容问题：服务端 `parse_image` 生成的 proposal 包含 `meta` 字段，但 `schema.json` 设 `additionalProperties: false`，导致 `/ai/execute` 重校验时拒绝该 proposal。**图片记账确认流程 100% 失败。**

---

## 总体评价

实现意图清晰、架构方向正确：
- 服务端 `parse_image` 完整覆盖了 attachment 所有权/状态校验、vision 模型调用、分类名解析、usage 记录
- 客户端 `QuickEntrySheet` 的解析状态机（idle → parsing → done/error）设计合理，草稿恢复可覆盖解析失败场景
- 13 个服务端测试覆盖主流程 + 边界 + 降级

但多个关键接口的契约一致性问题未打通，导致端到端流程不可用。

---

## P1 — 阻塞级（必须修复）

### R-M4-51：`meta` 字段被 schema 拒绝，图片解析确认 100% 失败

**文件：** `server/src/modules/ai/service.py:1133`、`server/src/modules/ai/validator.py`、`contracts/action-proposal/schema.json:46`
**说明：**

1. `parse_image` 在 proposal 中加入 `"meta": {"source": "image_parse", "attachmentId": ...}`
2. `schema.json` 根级 `additionalProperties: false`（L46），`meta` 不在 `properties` 中
3. `validate_proposal` 用 `jsonschema.Draft7Validator`，严格拒绝未知字段
4. `execute_proposal` 在 L578-587 重校验 → `rejected_schema` → 抛 `ai_proposal_rejected` 403

**结果：** 用户每张图片解析后点确认 → 服务端 403 → 记账失败。

**修复建议：** 在 `schema.json` 中新增 `meta` 属性定义（`type: object, additionalProperties: true`），或在 `parse_image` 返回前去掉 `meta`、改为客户端执行后单独调用附件链接端点。

---

### R-M4-52：AI 返回空 `bizDate` 时 execute 必失败

**文件：** `server/src/modules/ai/service.py:1206`、`server/src/modules/ai/prompts.py:111`
**说明：**

1. Prompt 指示 AI 无法识别日期时返回 `"bizDate": ""`（prompts.py:111）
2. `_resolve_categories` 中 `bizDate` 直接 `str(tx.get("bizDate", ""))` → 空字符串
3. `schema.json` 要求 `bizDate` 格式 `format: "date"` + `pattern: "^\\d{4}-\\d{2}-\\d{2}$"`
4. 空字符串不匹配 pattern → 校验失败 → `execute_proposal` 拒绝

**影响：** 小票/账单无日期时（大量场景），每笔交易 execute 失败。

**修复建议：** `_resolve_categories` 中当 `bizDate` 为空时，回退为当天日期（`datetime.date.today().isoformat()`），而非空字符串。

---

### R-M4-53：AI 未识别分类时 `categoryId: ""` 被 schema 拒绝

**文件：** `server/src/modules/ai/service.py:1204`
**说明：**

1. `_resolve_categories` 中分类未匹配时 `cat_id = ""`
2. `schema.json` TransactionItem 定义 `categoryId: { type: "string", minLength: 1 }`
3. 空字符串 `minLength: 1` 失败 → schema 校验不通过

**影响：** 用户有自定义分类且 AI 返回的分类名不在其中时（如 AI 返回"超市"但用户分类为"日用"），execute 失败。

**修复建议：** 未匹配分类时，要么：
- 回退到用户第一个支出分类 ID（`ctx.category_names` 中第一个 expense 类型）
- 或设为 `None`（不输出该字段），让客户端 BatchConfirmationCard 提示用户手动选择

---

### R-M4-54：零笔交易解析结果导致 UI 死区

**文件：** `app/src/features/ledger/QuickEntrySheet.tsx:54-65, 68-111`
**说明：**

1. AI 解析成功返回 `confidence: 0, transactions: []`
2. `setParseProposal(proposal)` — proposal 不为 null
3. `setParseState('done')`
4. `showBatchCard = parseState === 'done' && parseProposal` → `true`
5. 但 `BatchConfirmationCard` 的 `transactions.length === 0` → 组件 return null（L58）
6. 同时 `parseState === 'error'` 分支不渲染（因为 state 是 'done'，不是 'error'）

**结果：** 用户看到普通表单 + 附件缩略图，但无解析状态反馈——不知道解析已失败、无法重试、无法继续。

**修复建议：** 解析返回 0 笔交易时：
- 设 `parseState('error')` + `parseError('no_transactions')`，展示错误 UI 并提供重试/手动记账
- 或在 `showBatchCard` 判断中加 `parseProposal.transactions.length > 0`

---

## P2 — 重要（应修复）

### R-M4-55：`book.list` 重复调用 3+ 次

**文件：** `app/src/features/ledger/QuickEntrySheet.tsx:79, 161, 197, 256`
**说明：**

`handleImageParse`(L79)、`handleParseRetry`(L161)、`handleBatchConfirm`(L197)、`handleSubmit`(L256) 各自独立调用 `bridge.call('book.list', {})`。同一组件生命周期内多次桥调用不必要。

**修复建议：** 挂载时调用一次，存入 state/context；或直接通过 props 传入 bookId。

---

### R-M4-56：客户端不限制 parse 请求频率

**文件：** `app/src/features/ledger/QuickEntrySheet.tsx:68-111`
**说明：**

`useEffect` 监听 `image.attachment` 变化触发解析，`parsedAttachmentRef` 仅防重入。但如果：
- 用户上传图 A → 解析中 → 清除 → 上传图 A（同 attachmentId 不可能，但假设新 attachmentId）
- 或用户快速重试（虽然 ref 阻止了同 attachment 重复，但不同 attachment 无限制）

解析调用 120s 超时、消耗 vision 模型配额。无客户端节流。

**修复建议：** 添加 `parsing` 状态门控——解析中禁止重新触发；或服务端已有 rate limit 兜底，可忽略。

---

### R-M4-57：零交易 proposal 仍写入草稿并持久化

**文件：** `app/src/features/ledger/QuickEntrySheet.tsx:88-95`
**说明：**

解析返回 0 笔交易时，proposal 仍被保存到 localStorage 草稿（`saveDraft({ proposal: result.proposal })`）。下次打开组件时 `useEffect` 恢复此 proposal → BatchConfirmationCard return null → UI 死区跨会话持续。

**修复建议：** 解析结果为空时不保存 proposal 到草稿；保存前检查 `transactions.length > 0`。

---

### R-M4-58：`handleBatchConfirm` 直接 execute 跳过 validate

**文件：** `app/src/features/ledger/QuickEntrySheet.tsx:194-218`
**说明：**

`handleBatchConfirm` 直接调用 `bridge.executeProposal()`，跳过 `bridge.validateProposal()`。虽然服务端 `execute_proposal` 会重校验（L578），但客户端没有提前拦截无效提案的机会，错误只在 execute 失败后才暴露。

**影响：** 用户体验差——从"点确认"到"报错"之间没有中间反馈。

**修复建议：** 在 `handleBatchConfirm` 中先调 `bridge.validateProposal()`，通过后再 execute。或在 BatchConfirmationCard 确认前做本地 `validateProposal()` 检查。

---

### R-M4-59：`executeProposal` 缺少附件链接回退

**文件：** `app/src/features/ledger/QuickEntrySheet.tsx:202-206`
**说明：**

即使 R-M4-51 修复后 `meta.attachmentId` 被服务端接受并链接附件，当前代码也没有在 execute 成功后清除 `image.attachment` 状态。`image.attachment` 仍指向已上传的附件，ImageUploadView 仍显示缩略图。

用户确认记账后应该看到成功反馈（关闭 sheet 或 toast），不应停留在图片展示状态。

**修复建议：** execute 成功后调 `image.clear()` 或确保 `onSuccess()` 关闭 sheet。

---

## P3 — 低优先级

### R-M4-60：`BatchConfirmationCardProps.attachmentId` 未使用

**文件：** `app/src/features/ledger/BatchConfirmationCard.tsx:13`
**说明：** 接口定义了 `attachmentId?: string`，但组件体和父组件调用均未使用该 prop。死代码。

---

### R-M4-61：服务端 `parse_image` 全量读文件 + base64 膨胀

**文件：** `server/src/modules/ai/service.py:1037-1055`
**说明：** `file_path.read_bytes()` 全量读入后 `base64.b64encode()` 膨胀约 33%。4MB 图片 → ~5.3MB 内存。当前上限 4MB（`ai_image_max_bytes`），内存峰值可控。但若上限调高，需改为流式编码。

---

### R-M4-62：分类前缀匹配过于宽松

**文件：** `server/src/modules/ai/service.py:1195-1199`
**说明：** 模糊匹配逻辑 `name.startswith(cat_name) or cat_name.startswith(name)` 可能导致错误分类。例如：
- AI 返回"交" → 匹配"交通"（误匹配）
- 用户有"餐饮"和"餐饮食谱" → 两个都前缀匹配，结果取决于遍历顺序

**建议：** 只允许完全相等匹配；不匹配则回退默认分类或标记为未分类。

---

### R-M4-63：测试覆盖缺口

**文件：** `app/src/__tests__/store/image-draft-store.test.ts`
**说明：**
1. `image-draft-store.test.ts` 无 `parseError` / `proposal` 字段的测试（T-M4-04e 新增字段）
2. 无 `BatchConfirmationCard` 组件测试
3. 无 `QuickEntrySheet` 解析流程集成测试
4. 服务端 `test_image_parse.py` 13 个用例覆盖充分 ✅

---

## 确认无问题的设计点

| 方面 | 评价 |
|------|------|
| Attachment ownership + status 校验 | ✅ 跨用户 404、过期 410、已解析/已链接 409，完整正确 |
| Vision 模型重试 | ✅ `MAX_ATTEMPTS` 轮重试，invalid JSON 可重_parse；timeout 不重试 |
| Token 不泄漏 | ✅ `AiGatewayClient` Kotlin 层 token 注入，WebView 零接触 |
| 120s 超时设计 | ✅ Kotlin `IMAGE_PARSE_TIMEOUT_SECONDS = 120L`，TS 侧 `timeoutMs: 120_000`，对齐 |
| 草稿恢复流程 | ✅ `parseError`/`proposal` 区分上传失败与解析失败；组件恢复逻辑正确 |
| 解析失败 UI（非空交易场景） | ✅ 错误码映射 + 重试/手动记账按钮 |
| `render_image_messages` 多模态消息格式 | ✅ OpenAI-compatible `image_url` + `text` 数组格式 |
| Rate limit + daily quota | ✅ 双重保护；测试覆盖 rate limit 429 |
| 服务端 usage 记录 | ✅ prompt_tokens + completion_tokens 含重试累计 |

---

## 修复优先级排序

1. **立即修复（P1）：** R-M4-51/52/53/54 — 这四个问题导致图片记账确认流程完全不可用
2. **本次修复（P2）：** R-M4-55（book.list 去重）、R-M4-57（空 proposal 不写草稿）、R-M4-59（execute 后清状态）
3. **后续迭代（P3）：** R-M4-60~63 — 代码卫生与测试补齐

---

## 复验建议

修复后需验证的端到端场景：

1. **正常路径：** 拍照 → 上传 → 解析 → 显示 N 笔交易 → 编辑 → 确认 → execute 成功 → 附件已链接 → sheet 关闭
2. **无日期路径：** 小票无日期 → bizDate 回退当天 → 用户可编辑修改 → 确认成功
3. **无分类路径：** AI 返回未知分类 → 回退默认分类 → 用户在 BatchConfirmationCard 手动修改 → 确认成功
4. **零交易路径：** AI 无法识别 → 显示错误 UI → 用户可重试或手动记账
5. **跨用户隔离：** 用户 A 的附件用户 B 无法解析 → 404
6. **降级路径：** AI 服务不可用 → 503 → 草稿保存 → 恢复后重试
7. **过期附件：** 附件已过期 → 410 → 提示重新上传
