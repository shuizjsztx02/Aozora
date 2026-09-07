# T-M4-04 第六轮审查修复报告

**修复日期：** 2026-09-07
**基于：** `docs/review/T-M4-04-review-round6.md` 的 1×P1 + 10×P2 + 6×P3 发现
**测试结果：** ✅ Kotlin BUILD SUCCESSFUL（全部单测）；✅ TypeScript 545 passed / 33 files；✅ Python 31 passed

---

## 修复清单

### 🔴 P1 修复

| # | 文件 | 修复内容 |
|---|---|---|
| P1-1 | `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt:298-308` | `stopRecording` 在 `job?.cancelAndJoin()` 之后显式调 `record?.release()` 和 `rec?.close()`。根因是 `recognitionLoop.finally` 的身份检查 `audioRecord === record` 在 stopRecording 路径下失败（字段已被 stopRecording 置 null），必须由此路径自己释放。修正注释"finally 块统一释放"的错误描述。 |

### 🟡 P2 修复

| # | 文件 | 修复内容 |
|---|---|---|
| P2-1 | `VoiceRecognitionService.kt:50-54, 79-89, 104, 110` | 新增 `isInitializing` 标志位 + `synchronized(stateLock)` 检查，拦截并发 `initialize()` 调用。第二次调用直接返回，避免一个 `deleteRecursively()` 时另一个写入导致模型加载瞬态失败。 |
| P2-2 | `app/src/test/java/com/aozora/app/permission/PermissionManagerTest.kt:84-102` | 把原本 `DENIED \|\| PERMANENTLY_DENIED` OR 断言拆分为两个独立测试：`getStatus_afterRequestMarked_returnsDeniedOrPermanent`（钉死 PERMANENTLY_DENIED，因 Robolectric 无 UI 历史默认 rationale=false）+ `getStatus_withoutRequestMarked_returnsNotRequested`（覆盖未请求分支）。附注释说明 DENIED 需真实对话框历史，由 PERMANENTLY_DENIED 测试间接覆盖 if/else 两分支。 |
| P2-3 | `PermissionManagerTest.kt:108-122` | 新增 `@Config(sdk = [30])` 测试 `getAllStatuses_onApi30_returnsLegacyKeys`，验证 legacy 分支返回 `READ_EXTERNAL_STORAGE` + `NOTIFICATIONS_LEGACY=GRANTED`，且不含 `POST_NOTIFICATIONS`/`READ_MEDIA_IMAGES`。 |
| P2-4 | `app/src/main/java/com/aozora/app/permission/PermissionManager.kt:27-28, 37-38` | 类级 KDoc 加线程约束说明；`pendingCallback`/`pendingPermission` 字段注释解释为何不加 `@Volatile`（严格主线程访问）。 |
| P2-5 | `app/src/__tests__/features/ImagePickerSheet.test.tsx:103-119` | 新增测试 `'auto-continues pick after permission granted (R-M4-48)'`：点击拍照按钮 → 权限未授予 → 翻转权限 → 重渲染 → 断言 `onPick('camera')` 自动调用。文件头注释（line 8）现在属实。 |
| P2-6 | `app/src/__tests__/features/QuickEntrySheet.test.tsx:78-96, 114, 216-261` | `useImageDraft` mock 改为 mutable（`vi.hoisted`）；新增测试 `'recovers draft with parseError and calls bridge.parseImage on retry'`：设置带 `parseError` 的 draft → 挂载 → 断言恢复 UI 渲染（`image-parse-error`/`parse-retry`/`parse-dismiss` testid）→ 点 retry → 断言 `bridge.parseImage('att-draft', 'book-1')` 被调用。 |
| P2-7 | `QuickEntrySheet.test.tsx:263-300` | 新增测试 `'auto-triggers parseImage on mount when image.attachment is set and renders BatchConfirmationCard'`：预置 `mockImage.attachment` → 挂载 → await `batch-confirmation-card` testid → 断言 `bridge.parseImage('att-1', 'book-1')` 被调用。 |
| P2-8 | `app/src/__tests__/bridge/native-bridge.test.ts:731-756` | 新增两个测试：`'parseImage → image.parse 命令, payload 含 attachmentId 和 bookId'` + `'cancelImageParse → image.parse_cancel 命令, fire-and-forget'`。使用 snake_case `attachment_id`/`request_id` 匹配 `ParseImageResult`。 |
| P2-9 | `app/src/features/image/ImageUploadView.tsx:27` | 删除死错误码 `bridge_unavailable: '当前环境不支持'`（Kotlin/TS 侧均无路径产生此码）。 |
| P2-10 | `app/src/features/ledger/QuickEntrySheet.tsx:83` | mount effect 加 `// eslint-disable-line react-hooks/exhaustive-deps`，与 :161 风格一致。 |

### 🟢 P3 修复

| # | 文件 | 修复内容 |
|---|---|---|
| P3-2 | `app/src/platform/hooks/use-permissions.ts:86-88` | 更新 `if (!pending) refresh()` 注释——配合 P3-4 修复，此分支现可达（同步 GRANTED 短路返回 `{pending: false}` 时不触发 `onPermissionResult` 推送）。 |
| P3-3 | `app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt:617-624` | `handlePermissionsRequest` 加 `POST_NOTIFICATIONS` API<33 早返回：`{success: true, granted: true}`，避免返回误导性的 `invalid_input`。 |
| P3-4 | `AozoraBridge.kt:629-632` | `handlePermissionsRequest` 先检查 `manager.getStatus(androidPerm) == GRANTED`，已授予则同步返回 `{pending: false}`，避免多余系统对话框和语义不一致。 |
| P3-5 | `server/src/modules/attachments/service.py:250-263` | `write_bytes` OSError handler 加 `dest.unlink(missing_ok=True)`，清理部分写入残留文件，与 NEW-2 DB-flush 失败清理风格对称。 |
| P3-7 | `app/src/features/image/ImagePickerSheet.tsx:7, 23-36` | 移除无用的 `useCallback`（`onPick` 每次 render 都新建，memoization 永不命中），改为普通 async 函数。 |
| P3-8 | `QuickEntrySheet.tsx:212-213, 216, 218, 250-251` | `handleParseRetry` 加 `let cancelled = false` + 各 await 后 `if (cancelled) return` + catch 中 `if (code === 'cancelled' \|\| cancelled) return`，与主解析 effect 风格一致。 |

### 📝 文档更新

| 文件 | 修改 |
|---|---|
| `docs/milestones/production-execution-plan.md:624` | T-M4-04c 状态 → `✅ **通过**（第 6 轮 2026-09-07：NEW-1/NEW-2/Bug-C1 全部闭环；31 测试覆盖）` |
| 同上 :625 | T-M4-04d 状态 → `✅ **通过**（第 6 轮 2026-09-07：R-M4-46~50 修复闭环；ImageUploadView 四态 + 9 错误码测试覆盖）` |
| 同上 :626 | T-M4-04e 状态 → `✅ **通过**（第 6 轮 2026-09-07：handleImageParse + BatchConfirmationCard 已实现；parse → 批量确认闭环）` |

---

## 未修项（理由）

| # | 原因 |
|---|---|
| P3-1（isRecording 在 startRecording 前置 true） | 功能正确（stop 在未启动的 recorder 上被 catch 无害），属短暂不一致窗口，改动风险大于收益 |
| P3-6（service.py dead fallback） | 无害死分支，删除可能影响未来扩展，按"surgical changes"原则保留 |
| P3-9（旧观察：ImageClient readBytes / image-draft-store 无 TTL） | 已评估为低风险低影响，非本次修复范围 |

---

## 测试验证

```
Kotlin:    ./gradlew testDebugUnitTest  → BUILD SUCCESSFUL (32 tasks)
TypeScript: npx vitest run              → 33 files, 545 tests passed
Python:    pytest tests/test_attachments.py → 31 passed
```

---

## 结论

✅ **T-M4-04 第六轮审查修复完成，门禁全部通过**。

- 🔴 P1：资源泄漏已修，注释已修正
- 🟡 P2 × 10：全部修复（含 7 项新增测试覆盖原本缺口）
- 🟢 P3 × 9：修 6 项（P3-1/6/9 不修，理由如上）
- 📝 文档同步：production-execution-plan.md 中 04c/d/e 状态已更新

**T-M4-04 整体可合并。**
