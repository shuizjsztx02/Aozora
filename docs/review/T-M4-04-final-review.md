# T-M4-04 a/b/c/d 第四轮综合审查

审查日期：2026-09-06
审查范围：全栈——Kotlin (VoiceRecognitionService, AozoraBridge, PermissionManager, ImagePicker, ImageClient) + TypeScript (bridge-types.ts, ImagePickerSheet.tsx, QuickEntrySheet.tsx) + Python (service.py, routes.py, schemas.py, migration 0009, test_attachments.py) + 架构文档
审查依据：前三轮审查 (T-M4-04a-review.md, T-M4-04c-review.md, T-M4-04-abc-review.md, T-M4-04-abc-review-2.md, T-M4-04d-review.md)、逐条代码验证

---

## 总体评价

**T-M4-04a/b/c/d 整体可交付。前三轮审查中 2 项阻塞级 bug（Bug-B1 partial 转写失效、Bug-B2 runBlocking ANR）和全部 P1/P2 问题均已修复。仅剩 2 项 P3 未修（不影响功能）和 2 项遗留观察。**

| 子任务 | 评级 | 说明 |
|--------|------|------|
| T-M4-04a 权限状态机 | ✅ 通过 | 四态机稳健，TS 类型已对齐（Bug-A1 修复），仅 POST_NOTIFICATIONS 映射仍未启用（低优先级） |
| T-M4-04b 语音识别 | ✅ 通过 | Bug-B1/B2 两项阻塞级 bug 均已修复，模型内存泄漏已修（R-M4-41），Bug-B3 部分修复 |
| T-M4-04c 附件上传 | ✅ 通过 | R-M4-39/40/41/42/43 五项 P1/P2 全部修复，安全模型完整；R-M4-44/45 两项 P3 未修（不影响当前正确性） |
| T-M4-04d 客户端图片链路 | ✅ 通过 | R-M4-46~50 全部修复，草稿恢复和权限续接已落地 |

---

## 前三轮发现逐项验证

### T-M4-04b（语音识别）

| ID | 级别 | 原状态 | 现状态 | 验证说明 |
|----|------|--------|--------|----------|
| Bug-B1 | 🔴 Critical | partial 转写永不触发 | ✅ 已修复 | `VoiceRecognitionService.kt:377` 改用 `rec.partialResult`（返回 `{"partial": "..."}`），每次 `acceptWaveForm` 后无条件读取。逻辑正确。 |
| Bug-B2 | 🔴 High | runBlocking 主线程阻塞 | ✅ 已修复 | `stopRecording()` 改为 `suspend fun`（L233），用 `withContext(Dispatchers.IO)` + `cancelAndJoin()`。且先 `record?.stop()`（L262）解除 `read()` 阻塞再 join，避免 IO 协程卡住。 |
| Bug-B3 | 🟡 Medium | 无生命周期 CoroutineScope | ⚠️ 部分修复 | `initialize()` 现接受可选 `scope: CoroutineScope?` 参数（L55），由 AozoraBridge 传入 `serviceScope`。但 fallback 路径（L63-65）仍创建无绑定的 `CoroutineScope(Dispatchers.IO)`。实际场景中 AozoraBridge 总是传 scope，fallback 不触发，风险低。 |
| Bug-B4 | 🟡 Medium | voice error 双重 JSON 编码 | ✅ 已修复 | `dispatchVoiceError`（L758-763）现传裸对象字面量 `{code: $safeCode, message: $safeMessage}`，值经 `JSONObject.quote()` 安全转义。与 `dispatchImageProgress` 风格一致。 |
| R-M4-41 | 🟡 Medium | shutdown 不释放 Vosk 模型 | ✅ 已修复 | `shutdown()`（L321-323）现设 `serviceScope = null` 并调 `releaseModel()`。`finalize()` 安全网（L331-336）也已添加。 |

### T-M4-04a（权限状态机）

| ID | 级别 | 原状态 | 现状态 | 验证说明 |
|----|------|--------|--------|----------|
| Bug-A1 | 🟡 Low | TS PermissionStatusData 缺便捷字段 | ✅ 已修复 | `bridge-types.ts:316-321` 现含 `canRecordAudio`、`canUseCamera`、`canAccessImages`。Kotlin 侧（AozoraBridge.kt:578-583）发送这三个字段。 |
| N-M4-2 | 🟡 Low | POST_NOTIFICATIONS 映射存在但未启用 | ⚠️ 未变 | `permissionToKey()`（L627）映射 POST_NOTIFICATIONS，但 `getAllStatuses()`（L85-89）仍只返回 3 个权限（RECORD_AUDIO, CAMERA, image）。`permissions` record 中无 POST_NOTIFICATIONS key。便捷字段不涉及此权限，无功能影响。 |

### T-M4-04c（附件上传）

| ID | 级别 | 原状态 | 现状态 | 验证说明 |
|----|------|--------|--------|----------|
| R-M4-39 | 🔴 P1 | 启动清理误删 LINKED/PARSED | ✅ 已修复 | `cleanup_expired_files()`（L76-120）改为 DB 驱动：查 expired PENDING → 删文件+行；查 EXPIRED → 删文件+行；删无 DB 行的孤儿文件。**绝不触碰** LINKED/PARSED 行。测试 `test_cleanup_preserves_linked_files`（L514-541）验证 30 天老 LINKED 附件不被清理。 |
| R-M4-40 | 🟡 P2 | sniff 拒绝分支恒假 | ✅ 已修复 | 代码重构（L219-237）：header 检查后，`if final_mime is None: raise 415`，不再回退到 header。测试 `test_unknown_magic_bytes_rejected`（L195）和 `test_rejects_spoofed_header_when_body_is_not_image`（L232）均断言 415。 |
| R-M4-41 | 🟡 P2 | upload 全量 read 后才检查大小 | ✅ 已修复 | 分块读取（L207-217）：64KB buffer 循环 `read(65536)`，累计超 `MAX_UPLOAD_BYTES` 即 413。不再全量物化。测试 helper `_upload_bytes`（L125-146）支持 chunked `read(size)`。 |
| R-M4-42 | 🟡 P2 | 架构文档无 attachments | ✅ 已修复 | `data-model.md` 新增 `## attachments 表`（含列定义、状态机、保留策略）；`security.md` 新增上传存储安全面（大小、MIME 防伪、所有权、存储路径、生命周期）。 |
| R-M4-43 | 🟡 P2 | 迁移 0009 SQLite 兼容声明不实 | ✅ 已修复 | 迁移 docstring（L15-20）改为 `"PostgreSQL only"`，明确说明 `sa.UUID()` 仅 PG 支持。不再声称 SQLite 兼容。 |
| R-M4-44 | 🟢 P3 | link 交易校验未过滤软删 Book | ❌ 未修 | `_verify_transaction_ownership`（L154-169）仍无 `Book.deleted_at.is_(None)` 过滤。不影响跨用户安全（owner check 在），属生命周期口径不一致。 |
| R-M4-45 | 🟢 P3 | 代码卫生四项 | ❌ 未修 | ①schemas.py:12-16 死枚举仍在；②service.py:293-298 `get_attachment` 重复 expiry flip 仍在；③test L428-431 误导注释仍在；④`list_attachments` 无分页仍在。均不影响当前正确性。 |
| Bug-C1 | 🟡 Medium | migration 无 backfill | ⚠️ 已跟踪 | 迁移 L51-57 添加 TODO 注释，列出 backfill 步骤。生产部署前须执行。 |

### T-M4-04d（客户端图片链路）

| ID | 级别 | 原状态 | 现状态 | 验证说明 |
|----|------|--------|--------|----------|
| R-M4-46 | 🔴 P1 | image.pick 超时 | ✅ 无需修复 | 代码已正确传 `timeoutMs: 0`，`call` 方法 `timeout > 0` 判断不设定时器。 |
| R-M4-47 | 🟡 P2 | 草稿只写不读 | ✅ 已修复 | 写草稿移至 `handleImagePick` catch 块（QuickEntrySheet.tsx:44-54），不再依赖 useEffect。`useImageDraft()` 在 QuickEntrySheet 中消费（L35），恢复 UI（L227-255）提供重试/放弃。 |
| R-M4-48 | 🟡 P2 | 权限请求后不自动续接 | ✅ 已修复 | `pendingSource` 状态（ImagePickerSheet.tsx:18）+ `useEffect`（L38-45）监听权限变化，授予后自动调 `onPick`。 |
| R-M4-49 | 🟢 P3 | guessMime 误导 | ✅ 已修复 | `ImageClient.kt:168-169` 硬编码 `"image/jpeg"`，`guessMime` 已删除。 |
| R-M4-50 | 🟢 P3 | pendingCallback 重入 | ✅ 已修复 | `ImagePicker.kt:90-93` 用 `getAndSet` 检查旧 callback，存在则调 `null` 触发 `PickerCancelled`。 |

---

## 新发现

本轮逐文件重新审查未发现新的 P1/P2 级问题。

### 低优先级观察（不阻塞合并）

| 编号 | 文件 | 说明 |
|------|------|------|
| Obs-1 | `VoiceRecognitionService.kt:373` | `recognitionLoop` 中每次 `acceptWaveForm` 后读 `partialResult`，但从不读 `result`（句子边界结果）。Vosk 的 `partialResult` 会在句子完成时清空，意味着积累文本（accumulated text）只能通过 `finalResult` 在 stop 时获取。功能上可工作（前端用 `onFinalResult` 获取完整文本），但用户如期望实时看到已确认句子拼接，需后续迭代。 |
| Obs-2 | `service.py:293-298` | `get_attachment` 调 `_get_owned`（内部做 expiry flip）后又调 `_mark_expired_if_needed`——无害冗余（R-M4-45 子项，已标记待修）。 |

---

## 门禁复核

| 门禁 | 说明 |
|------|------|
| Bug-B1/B2 修复验证 | ✅ 代码事实与修复描述一致，逻辑正确 |
| R-M4-39~43 修复验证 | ✅ 代码事实与修复描述一致，测试覆盖充分 |
| R-M4-46~50 修复验证 | ✅ 代码事实与修复描述一致 |
| 测试覆盖 | test_attachments.py 含 28+ 用例覆盖 DB-driven cleanup、sniff 拒绝、chunked read、LINKED 保留 |
| 架构文档 | data-model.md + security.md 已补 attachments 相关内容 |

---

## 结论

✅ **审查通过**。前三轮审查中 2 项阻塞级 bug 和全部 P1/P2 问题均已修复，安全模型（MIME sniff + chunked read + ownership + DB-driven cleanup）完整可靠。

**遗留项（均不阻塞合并，按优先级排序）：**

1. 🟢 **R-M4-44** — `_verify_transaction_ownership` 补 `Book.deleted_at.is_(None)`（P3，生命周期一致性）
2. 🟢 **R-M4-45** — 死枚举删除、重复 flip 去除、测试注释修正、分页补充（P3，代码卫生）
3. ⚠️ **Bug-B3** — `initialize()` fallback 路径仍有无绑定 scope（实际不触发，可忽略或随手修）
4. ⚠️ **Bug-C1** — 生产部署前执行 migration backfill（已 TODO 跟踪）
5. ⚠️ **N-M4-2** — `getAllStatuses()` 未含 POST_NOTIFICATIONS（低优先级，不影响当前功能）
