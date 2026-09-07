# T-M4-04e 代码审查（第 3 轮·综合复审）

**审查日期：** 2026-09-07
**审查范围：** T-M4-04e 全栈——图片上传 → AI 解析 → 批量确认 → 附件链接
**审查文件：**
- 客户端：`QuickEntrySheet.tsx`、`BatchConfirmationCard.tsx`、`ImagePickerSheet.tsx`、`ImageUploadView.tsx`、`use-image.ts`、`image-draft-store.ts`、`native-bridge.ts`、`bridge-types.ts`
- Android：`AozoraBridge.kt`（handleImageParse）、`AiGatewayClient.kt`（parseImage）
- 服务端：`service.py`（parse_image / _resolve_categories / execute_proposal 附件链接）、`routes.py`、`schemas.py`
- 契约：`schema.json`、`action-proposal.ts`
- 测试：`test_image_parse.py`（17 用例）、`QuickEntrySheet.test.tsx`（6 用例）
**审查依据：** 前两轮 review（T-M4-04e-review.md、T-M4-04e-review-round2.md）+ 逐行代码验证

---

## 总体评价

**✅ 通过（附 2 个 P2 + 3 个 P3）。** 端到端链路完整可靠，前两轮 4 个 P1 + 5 个 P2 全部修复到位。第 3 轮新发现 2 个 P2（竞态 + 零交易重试死路）和 3 个 P3，无新 P1。

---

## 前两轮问题复验

### P1 复验（全部 ✅）

| ID | 问题 | 状态 | 验证 |
|----|------|------|------|
| R-M4-51 | `meta` 字段被 schema 拒绝 | ✅ | `schema.json` L45-48 定义 `meta: { type: object }`；`action-proposal.ts` L58 同步 `meta?: Record<string, unknown>` |
| R-M4-52 | 空 `bizDate` 导致 execute 失败 | ✅ | `service.py` L1346-1350：空/格式错误均回退 `today_str`；测试 `test_multi_transaction_batch` 显式断言 `len == 10` |
| R-M4-53 | 未识别分类 `categoryId: ""` 被拒 | ✅ | L1342-1344 回退 `fallback_category_id`；测试断言 `txs[2]["categoryId"] != ""` |
| R-M4-54 | 零交易解析结果导致 UI 死区 | ✅ | `QuickEntrySheet.tsx` L108-121：零交易设为 error 状态，proposal 置 null，草稿不存 proposal |

### P2 复验（全部 ✅）

| ID | 问题 | 状态 | 验证 |
|----|------|------|------|
| R-M4-55 | `book.list` 重复调用 | ✅ | L52-59 `bookIdRef` 缓存，挂载周期内只调一次 |
| R-M4-56 | 客户端不限制 parse 频率 | ✅ 可接受 | `parsedAttachmentRef` 防同 attachment 重复；服务端 rate limit 兜底 |
| R-M4-57 | 零交易 proposal 仍写草稿 | ✅ | L111-121：零交易时 `proposal: null` 写草稿 |
| R-M4-58 | `handleBatchConfirm` 跳过 validate | ✅ | L269-276：execute 前先调 `bridge.validateProposal` |
| R-M4-59 | `executeProposal` 后未清图片状态 | ✅ | L286-290：`image.clear()` → `clearDraft()` → `onSuccess()` |

### P3 复验

| ID | 问题 | 状态 |
|----|------|------|
| R-M4-64 | 零分类 fallback `""` | ✅ **已修复** | `service.py` L1311-1316：零分类时直接抛 `AppException(400, "no_categories", ...)`；测试 `test_zero_categories_raises_clear_error` 覆盖 |
| R-M4-65 | `handleBatchConfirm` 校验失败无反馈 | ✅ **已修复** | L273-275：设 `setBatchError(...)` 并在 UI 展示（L420-429） |
| R-M4-60 | `BatchConfirmationCardProps.attachmentId` 死代码 | ✅ **已修复** | 当前 props 无 `attachmentId` 字段 |

---

## 新发现问题

### P2 — 建议本轮修复

#### NEW-1：解析中换图竞态——useEffect 缺 cleanup 导致 stale parse 覆盖新结果

**文件：** `app/src/features/ledger/QuickEntrySheet.tsx:86-152`

**问题：**
自动解析的 `useEffect`（L86）声明了 `let cancelled = false`（L93），但 **没有 cleanup 函数** 将其设为 `true`。当用户在解析过程中选择新图片时：

1. 图片 A 上传完成 → useEffect 触发 → `cancelled_A = false` → `parseImage(A)` 开始
2. 用户不等解析完，选择新图片 B → `handleImagePick` 重置 `parsedAttachmentRef.current = null`，清空 parseState
3. 图片 B 上传完成 → useEffect 触发 → `cancelled_B = false` → `parseImage(B)` 开始
4. A 的解析返回 → `cancelled_A` 仍为 `false`（无 cleanup 设置）→ 覆盖 `parseProposal` 为 A 的结果
5. B 的解析返回 → 覆盖为 B 的结果

**后果：**
- 步骤 4-5 之间，用户短暂看到 A 的批量确认卡（UI 闪烁）
- 两笔 vision model 调用均计费（token 浪费）
- `cancelled` 标志形同虚设——L101/L103 的检查是死代码

**复现条件：** 网络良好时 parse ~3-5s，用户在此窗口内换图即触发。

**修复：** 加 cleanup 函数：
```typescript
useEffect(() => {
    // ...existing code...
    let cancelled = false
    const doParse = async () => { /* ... */ }
    doParse()
    return () => { cancelled = true }  // ← 补这一行
}, [image.attachment])
```

---

#### NEW-2：零交易解析后"重试识别"必败——服务端已将 attachment 标记为 PARSED

**文件：** `server/src/modules/ai/service.py:1253` + `app/src/features/ledger/QuickEntrySheet.tsx:583`

**问题：**
当 vision model 返回零交易时，服务端仍然执行 L1253 `attachment.status = AttachmentStatus.PARSED`（因为 L1252-1254 在 L1220 提取 raw_transactions 之后无条件执行）。客户端（L108-121）检测到零交易后显示"重试识别"按钮。

但用户点击"重试识别"时，`handleParseRetry`（L196）调用 `bridge.parseImage(attId, bookId)`，服务端 `parse_image` 检查 `attachment.status != PENDING`（L1111）→ 返回 **409 attachment_not_pending**。

**后果：** 用户在零交易错误页面看到"重试识别"按钮，点击后必定失败，显示"识别失败"。无恢复路径（除非重新上传一张图片）。

**复现路径：** 拍一张无小票的照片 → AI 返回 0 笔交易 → 服务端 status=PARSED → 客户端显示"未识别到消费记录"+ 重试按钮 → 点击重试 → 409 → "识别失败"

**修复方案（二选一）：**
- **(a) 服务端：** `parse_image` 仅在 `resolved_txs` 非空时才设置 `status = PARSED`。零交易时保持 PENDING，允许重试。
- **(b) 客户端：** 零交易时不显示"重试识别"，改为"重新选择"（引导用户换新图片）。
- 建议 (a)+(b) 都做：(a) 修复根本问题，(b) 改善 UX。

---

### P3 — 本轮不修复

#### NEW-3：`handleBatchConfirm` 执行失败无用户反馈

**文件：** `QuickEntrySheet.tsx:291-293`

**说明：** `executeProposal` 抛异常时，`catch` 块仅 `console.error`，不设 `batchError`。按钮停止旋转但用户不知道发生了什么。与 R-M4-65（校验失败反馈）同模式但不同代码路径。

**建议：** `catch` 块中加 `setBatchError(err.message || 'execution_failed')`。

---

#### NEW-4：`handleImagePick` 未清除 `batchError`

**文件：** `QuickEntrySheet.tsx:154-173`

**说明：** 用户选新图片时，`handleImagePick` 重置 `parseState`/`parseProposal`/`parseError`，但 **未重置 `batchError`**。若之前有校验失败提示，选新图后 parse 成功显示批量确认卡，旧的 `batchError` 仍可见。

对比：`handleImageClear`（L299-307）和 `handleImagePickAgain`（L309-317）都正确重置了 `batchError`。

**修复：** 在 `handleImagePick` 开头加 `setBatchError(null)`。

---

#### NEW-5：`no_categories` 错误码客户端无专属提示

**文件：** `QuickEntrySheet.tsx:576-578`

**说明：** 服务端返回 `no_categories`（NEW-E6 修复）时，客户端落入通用 "识别失败" 分支。用户不知道需要先创建分类。

**建议：** 在错误显示逻辑中加一条：
```typescript
parseError === 'no_categories' ? '请先创建至少一个分类' : ...
```

---

## 确认无问题的关键设计点

| 设计点 | 验证 |
|--------|------|
| `meta.attachmentId` → execute 自动链接附件 | ✅ `service.py` L755-776：从 `proposal.meta.attachmentId` 取出 → `link_attachment`；`link_attachment` 允许 PARSED 状态（仅拒 EXPIRED/LINKED），parse 后的附件可正常链接 |
| 幂等执行 | ✅ `proposalId` 作为幂等键（L637-657），并发竞争由 unique constraint 兜底（L729-746） |
| 并发 parse 去重 | ✅ `_parsing_attachments` 内存集合 + finally 释放（L1144-1279）；测试 `test_duplicate_parse_returns_409` 覆盖 |
| 分类解析优先级 | ✅ 精确匹配 → 前缀匹配 → fallback（L1332-1344）；`test_category_prefix_match` 验证精确优先 |
| 草稿恢复完整链路 | ✅ 挂载时检查 `draft.proposal`（L70-83）→ 恢复批量确认卡；`draft.parseError`（L79-81）→ 恢复错误状态；`draft.attachment`（L76-78）→ 恢复缩略图 |
| 图片上传 generation 竞态保护 | ✅ `use-image.ts` L62/76/88/95/116/127：generation counter 防止 `clear()` 和 `pickImage()` 互相覆盖 |
| 权限续接 `pendingSource` | ✅ `ImagePickerSheet.tsx` L18/28-37/41-48：记录待处理源 → 权限授予后 `onPickRef.current` 自动续接；`onPickRef` 避免 effect 依赖不稳定 |
| vision 超时预算 | ✅ 客户端 120s（`native-bridge.ts` L417）< Android 侧 `IMAGE_PARSE_TIMEOUT_SECONDS`（`AiGatewayClient.kt`）；服务端 `ai_timeout_seconds` × MAX_ATTEMPTS(2) < 120s |
| schema 与 TS 类型一致 | ✅ `meta` 两侧都有（schema.json L45-48 / action-proposal.ts L58）；`TransactionItem` required 字段对齐 |
| `_resolve_categories` 数据清洗 | ✅ 金额零/负/非数字跳过（L1353-1368）；bizDate 空/格式错误回退 today（L1347-1350）；type 非枚举归一化为 expense（L1371-1373） |

---

## 测试覆盖评估

| 范围 | 覆盖 | 缺口 |
|------|------|------|
| `test_image_parse.py`（17 用例） | ✅ 单/多笔解析、分类匹配、ownership、过期、重解析、降级、去重、数据清洗、零分类 | 无"零交易 → 重试 409"场景（NEW-2） |
| `QuickEntrySheet.test.tsx`（6 用例） | ✅ 渲染、切换、提交、上传状态 | 无 parse → batch 流程集成测试；无草稿恢复测试 |
| `ImagePickerSheet.test.tsx`（8 用例） | ✅ 渲染、权限、续接、取消 | — |
| `ImageUploadView.test.tsx`（14 用例） | ✅ 四态渲染 | — |
| `image-draft-store.test.ts`（5 用例） | ✅ 基本 CRUD | 无 `parseError`/`proposal` 字段测试 |
| `test_narrative_report.py` | ✅ | — |

---

## 修复优先级总结

| 级别 | ID | 问题 | 建议 |
|------|-----|------|------|
| **P2** | NEW-1 | parse useEffect 缺 cleanup → 竞态覆盖 | 加 `return () => { cancelled = true }` |
| **P2** | NEW-2 | 零交易重试必败（服务端已 PARSED） | 服务端：零交易不改状态；客户端：改按钮文案 |
| P3 | NEW-3 | execute 失败无用户反馈 | catch 块加 `setBatchError` |
| P3 | NEW-4 | `handleImagePick` 未清 `batchError` | 加 `setBatchError(null)` |
| P3 | NEW-5 | `no_categories` 无专属提示 | 错误显示加分类引导 |

---

## 复验建议（端到端场景）

1. ✅ 正常路径：拍照 → 解析 → N 笔交易 → 编辑 → 确认 → execute → sheet 关闭 → 附件已链接
2. ✅ 无日期路径：AI 返回空 bizDate → 回退当天 → 确认成功
3. ⚠️ 零交易路径：AI 无法识别 → "未识别到消费记录" → **重试识别必败**（NEW-2）→ 需修
4. ⚠️ 换图竞态：解析中换图 → 短暂看到旧结果（NEW-1）→ 需修
5. ✅ 零分类用户：服务端 400 → 客户端显示"识别失败"（应引导创建分类，NEW-5）
6. ✅ 草稿恢复：关闭弹层 → 重开 → 批量确认卡恢复
7. ✅ 权限续接：首次拍照 → 授权 → 自动进入相机
