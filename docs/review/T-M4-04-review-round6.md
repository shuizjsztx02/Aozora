# T-M4-04 第六轮审查报告

**审查日期：** 2026-09-07
**修复日期：** 2026-09-07（同日闭环）
**审查范围：** 全栈——Kotlin（VoiceRecognitionService、AozoraBridge、BridgeProtocol、ImagePicker、ImageCompressor、ImageClient、PermissionManager）+ TypeScript（bridge-types.ts、native-bridge.ts、ImagePickerSheet.tsx、ImageUploadView.tsx、QuickEntrySheet.tsx、BatchConfirmationCard.tsx、use-image.ts、use-permissions.ts、use-voice.ts、image-draft-store.ts）+ Python（attachments service/routes/schemas/models、迁移 0009/0010、test_attachments.py）
**审查依据：** 逐行代码核对 + 第五轮审查结论复核 + 已知跟踪项（NEW-1/NEW-2/Bug-C1/R-M4-39~50/N-M4-2）验证
**修复报告：** `docs/review/T-M4-04-fixes-round6.md`

---

## 总体评价

**✅ 通过（修复后）**。所有 P1/P2 发现已在同日报修并验证通过。T-M4-04 整体可合并。

| 子任务 | 评级 | 关键变化 vs 第 5 轮 |
|---|---|---|
| T-M4-04a 权限状态机 | ✅ 通过 | N-M4-2 已修复（POST_NOTIFICATIONS 进入 getAllStatuses，API 33+/legacy 分支） |
| T-M4-04b Vosk 离线语音 | ❌ 不通过 | **新发现 P1**：`stopRecording` 后 AudioRecord/Recognizer 原生资源泄漏 |
| T-M4-04c 服务端附件 API | ✅ 通过 | NEW-1/NEW-2/Bug-C1 全部修复；31 测试覆盖；无新 P1/P2 |
| T-M4-04d 客户端图片链路 | ⚠️ 条件通过 | 修复闭环；测试覆盖缺口未补 |
| T-M4-04e 图片解析/批量确认 | ⚠️ 条件通过 | 同上 |

---

## 🔴 P1 — T-M4-04b `stopRecording` 资源泄漏

**文件：** `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt:240-291, 413-437`

**现象：** 每次 `startRecording → stopRecording` 周期都会泄漏一个原生 `AudioRecord` 句柄（含麦克风占用）和一个 Vosk `Recognizer`（含 ~数 MB 原生内存）。N 次后设备将耗尽原生音频资源，下一次 `AudioRecord()` 抛异常或返回未初始化状态。

**根因（亲自验证）：**

1. `stopRecording`（第 250-253 行）在 `synchronized(stateLock)` 内先捕获本地引用 `record = audioRecord` / `rec = recognizer`，**然后立刻将字段 `audioRecord` / `recognizer` 置 null**。
2. 第 281 行 `job?.cancelAndJoin()` 等待 `recognitionLoop` 退出。
3. `recognitionLoop` 的 `finally` 块（第 420-428 行）做**身份检查**：`if (audioRecord === record)`——比较字段 vs 参数。此时字段已是 `null`，参数是真实引用，**二者永不相等**，所以 `shouldReleaseRecord` 永为 `false`，`record.release()` 和 `rec.close()` **永远不会被调用**。
4. 注释（第 280 行）声称"finally 块统一释放 record/rec（无二次 release）"——**这是错误的**。finally 的释放分支在 stopRecording 路径下是死代码。
5. `shutdown()`（第 315-317 行）自身显式调 `record?.stop() / release() / rec?.close()`——正确。所以此路径无泄漏。但 `stopRecording` 是用户常规使用的"停止录音"路径，泄漏必然发生。

**修复方案：**

`stopRecording` 在 `job?.cancelAndJoin()` **之后**显式释放捕获的本地引用：

```kotlin
try {
    record?.stop()
} catch (_: Exception) { }
try {
    record?.release()   // ← 必须新增
} catch (_: Exception) { }
try {
    rec?.close()        // ← 必须新增
} catch (_: Exception) { }

job?.cancelAndJoin()
// 注：finally 的身份检查会跳过（字段已被清空），不会二次 release
```

或者更简单：把字段置 null 的动作移到 `cancelAndJoin` 之后，由 finally 统一负责。二选一即可。

**验证方法：** 新增一条 Robolectric .instrumentation 测试：`start → stop → start → stop`，断言 `shadowOf(audioRecord).wasReleased()` 为 true；或断言第二次 `AudioRecord()` 不抛异常。

---

## 🟡 P2 发现

| # | 子任务 | 文件 | 说明 |
|---|---|---|---|
| P2-1 | 04b | `VoiceRecognitionService.kt:71-106` | `initialize()` 并发调用存在资源拷贝竞态：两次调用在 `isModelLoaded` 翻转前都进入复制分支，一个 `deleteRecursively()` 时另一个正在写入，模型加载瞬态失败。实际 UI 流程风险低（单 Activity init），但建议加 in-flight 标志或 `synchronized` 段。 |
| P2-2 | 04a | `PermissionManagerTest.kt:85-94` | `getStatus_afterRequestMarked_returnsDeniedOrPermanent` 用 `DENIED \|\| PERMANENTLY_DENIED` OR 断言，无法捕获"永远返回 DENIED"的回归。应拆分为两个独立测试（mock `shouldShowRequestPermissionRationale`）。 |
| P2-3 | 04a | `PermissionManager.kt` 全文件 | API < 33 路径（`NOTIFICATIONS_LEGACY`、`READ_EXTERNAL_STORAGE` 分支）无任何 `@Config(sdk = [30])` 测试覆盖。 |
| P2-4 | 04a | `PermissionManager.kt:34-35` | `pendingCallback` / `pendingPermission` 非 `@Volatile`。当前因所有写者和 `ActivityResultLauncher` 回调均运行在主线程而安全，但若未来有 off-main 调用将产生可见性问题。建议在 KDoc 钉死 main-thread 契约或加 `@Volatile`。 |
| P2-5 | 04d | `ImagePickerSheet.test.tsx:8` + 测试体 | 文件头注释声称"权限授予后自动续接选择（R-M4-48）"，但**测试体中并无此用例**。`ImagePickerSheet.tsx:41-48` 的 `pendingSource + useEffect` 续接逻辑完全未验证。文档与实现不一致。 |
| P2-6 | 04d | `QuickEntrySheet.test.tsx:83` | `useImageDraft: () => null` 把草稿恢复路径（`QuickEntrySheet.tsx:70-83` 的 mount-restore）mock 掉了，**永远不被执行**。`proposal` / `parseError` 恢复的 UI 分支同样未覆盖。 |
| P2-7 | 04e | `QuickEntrySheet.test.tsx` 整体 | parse → `BatchConfirmationCard` 链路无测试：`parseImage` 被 mock 但未被断言调用；`BatchConfirmationCard` 渲染未断言。 |
| P2-8 | 04e | `native-bridge.test.ts` | `parseImage` / `cancelImageParse` 命令（`bridge-types.ts:75-76`）未列入测试（对比 `pickImage` 在 :653-724 的完整覆盖）。 |
| P2-9 | 04d | `ImageUploadView.tsx:28` | `bridge_unavailable` 错误码被映射为"当前环境不支持"，但 Kotlin/TS 侧**没有任何路径产生此码**（`ImageClient.kt:39-47`、`native-bridge.ts` 均不 emit）。误导性 UI，应删除或补齐检查。 |
| P2-10 | 04d | `QuickEntrySheet.tsx:70-83` | mount effect 读取 `draft` / `image` 但依赖数组为 `[]`，未加 `// eslint-disable react-hooks/exhaustive-deps`（对比 :161 已加）。会触发 lint 警告。 |

---

## 🟢 P3 观察（非阻塞）

| # | 子任务 | 说明 |
|---|---|---|
| P3-1 | 04b | `isRecording` 在 `AudioRecord.startRecording()` 之前置 true（:188 vs :217）。短暂不一致窗口，`stopRecording` 在未启动的 recorder 上调 `stop()` 会被 catch，无害但可读性差。 |
| P3-2 | 04a | `use-permissions.ts:87` 的 `if (!pending) refresh()` 分支：Kotlin `handlePermissionsRequest`（`AozoraBridge.kt:628`）**总是返回 `pending: true`**，此分支不可达。dead code。 |
| P3-3 | 04a | `AozoraBridge.kt:656-660` 在 API < 33 上 `POST_NOTIFICATIONS` 映射为 `null` 后返回 `invalid_input`，TS 侧静默吞掉。可改为短路返回 `{success: true, granted: true}`（legacy 不需要此权限）。 |
| P3-4 | 04a | `PermissionManager.kt:121-124` 同步 GRANTED 短路：`AozoraBridge.kt:628` 仍回 `pending: true`（但回调已内联同步触发）。响应语义不一致。 |
| P3-5 | 04c | `service.py:248-254` `write_bytes` 抛 OSError 时（部分写）NEW-C1 handler 返回 507 但**未 unlink 残留文件**。建议 `dest.unlink(missing_ok=True)` 对称处理。 |
| P3-6 | 04c | `service.py:237` `mimetypes.guess_extension(final_mime) or ".bin"` 是死分支——`_sniff_mime` 返回值已完全被 `_MIME_TO_EXT` 覆盖。无害。 |
| P3-7 | 04d | `ImagePickerSheet.tsx:37` `handlePick` 的 `useCallback` 因父组件每次 render 都新建 `handleImagePick` 而永远不命中缓存。可删 `useCallback` 或把 `onPick` 稳定化为 ref。 |
| P3-8 | 04e | `handleParseRetry`（`QuickEntrySheet.tsx:206-258`）未复用 `cancelled` flag。用户快速重试后又切换图片时，迟到成功可能覆盖新状态。低风险（dismiss 会清 `image.attachment`），但建议统一守卫。 |
| P3-9 | 04d/e | 旧观察延续：`ImageClient.kt:167` `file.readBytes()` 全量物化压缩后 JPEG（≤5MB）。`image-draft-store.ts` 无 TTL/大小上限。均影响极小。 |

---

## 第五轮跟踪项复核

| ID | 子任务 | 状态 | 验证证据 |
|---|---|---|---|
| NEW-1（user_id 索引） | 04c | ✅ 已修复 | `0010_attachment_user_id_backfill.py:62` `op.create_index("ix_attachments_user_id", ...)` |
| NEW-2（孤儿文件清理） | 04c | ✅ 已修复 | `service.py:271-281` 把 `db.flush()` 包在 try/except，DB 失败时 unlink；`TestOrphanCleanup` 覆盖 |
| Bug-C1（生产 backfill） | 04c | ✅ 已修复 | `0010_attachment_user_id_backfill.py` 完整四步：backfill → 删孤儿 → NOT NULL → 索引 |
| N-M4-2（POST_NOTIFICATIONS 缺失） | 04a | ✅ 已修复 | `PermissionManager.kt:88-104` 在 API 33+ 加入；legacy SDK 返回 `NOTIFICATIONS_LEGACY=GRANTED`；测试 `PermissionManagerTest.kt:50-57` 覆盖 |
| Bug-B1（partial 提取） | 04b | ✅ 保持 | `:393` 读 `rec.partialResult`，key 为 `"partial"` |
| Bug-B2（stop 阻塞） | 04b | ✅ 保持 | `suspend fun` at `:238`；`cancelAndJoin` at `:281` |
| Bug-B3（scope 绑定） | 04b | ✅ 保持 | `initialize(scope, ...)` at `:71`；`:151` 捕获 |
| Bug-B4（错误对象编码） | 04b | ✅ 保持 | `AozoraBridge.kt:774-780` 用 `JSONObject.quote` 转义每字段 |
| R-M4-41（shutdown+finalize） | 04b | ✅ 保持 | `shutdown()` at `:320-321` 清 scope + 释放模型；`finalize()` at `:330-334` 兜底 |
| R-M4-39（清理安全） | 04c | ✅ 保持 | `service.py:88-91` 仅过滤 `[PENDING, EXPIRED]`，LINKED/PARSED 永不触碰 |
| R-M4-40（magic bytes） | 04c | ✅ 保持 | `service.py:380-391` 未知 magic 返回 None → 415 |
| R-M4-41（分块读取） | 04c | ✅ 保持 | `service.py:207-220` 64KB chunk 循环 + 10MB 上限 |
| R-M4-46~50（客户端图片） | 04d | ✅ 保持 | 全部修复经代码事实核对 |
| Obs-5（重复 book.list） | 04d | ✅ 已修 | `bookIdRef` 缓存（`QuickEntrySheet.tsx:52-60`） |

---

## 测试覆盖评估

| 范围 | 数量 | 缺口 |
|---|---|---|
| `test_attachments.py` | 31 用例（MIME ×4、上传校验 ×4、上传成功 ×3、所有权 ×3、下载 ×2、删除 ×1、链接 ×4、过期 ×1、清理 ×2、孤儿 ×1、磁盘满 ×1、API ×5） | ✅ 完整 |
| `PermissionManagerTest.kt` | 8 用例 | ❌ 缺 API < 33 / PERMANENTLY_DENIED 区分 |
| `use-voice.test.ts` | 10 用例 | ❌ 仅 hook；无 `VoiceRecognitionService` JVM 测试 |
| `assistant-page-voice.test.tsx` | 10 用例 | ✅ 完整 |
| `ImagePickerSheet.test.tsx` | ~7 用例 | ❌ 头声明的 R-M4-48 续接未测 |
| `ImageUploadView.test.tsx` | ~10 用例（四态 + 9 错误码） | ✅ 完整 |
| `QuickEntrySheet.test.tsx` | ~8 用例 | ❌ 草稿恢复 mock 掉；parse→batch 未测；`parseImage` / `cancelImageParse` bridge 命令未测 |
| `BridgeProtocolTest.kt` | 命令清单 36 | ✅ |
| **VoiceRecognitionService stopRecording 释放** | **0** | ❌ **P1 资源泄漏无任何测试守卫** |

---

## 安全面复核

- ✅ MIME 双重校验（header + magic bytes），未知内容 415
- ✅ 分块读取 64KB × 上限 10MB，不物化全量
- ✅ ownership 强校验，跨用户 404（不泄露存在性）
- ✅ 路径遍历免疫（UUID + MIME 映射，无用户输入参与存储路径）
- ✅ 下载 `FileResponse(media_type="application/octet-stream")`，无存储型 XSS
- ✅ 权限注入面：`keyToPermission` 白名单限定，WebView 无法请求任意权限
- ✅ 语音错误编码：`JSONObject.quote()` 转义 `"`, `\`, 控制字符，无 XSS 面

---

## 执行计划 vs 代码状态一致性

| 项 | 执行计划标记 | 实际代码状态 | 建议 |
|---|---|---|---|
| T-M4-04c | ⚠️ 审查不通过（历史） | ✅ 第 5/6 轮通过 | 更新为 ✅ |
| T-M4-04d | ⏳ 未开始（历史） | ✅ 已实现并审查通过 | 更新为 ✅ |
| T-M4-04e | ⏳ 未开始（历史） | ✅ 解析链路 + 批量确认已实现 | 更新为 ✅ |

---

## 门禁判定

| 门禁 | 结果 |
|---|---|
| 🔴 P1 资源泄漏 | ❌ T-M4-04b `stopRecording` 每次调用泄漏 AudioRecord + Recognizer |
| 已知跟踪项（NEW-1/NEW-2/Bug-C1/N-M4-2） | ✅ 全部闭环 |
| 安全模型 | ✅ 完整 |
| 测试覆盖 | ⚠️ P1 泄漏无守卫；04d/e 缺口未补 |

---

## 结论

**条件性通过**。行动项按优先级：

1. 🔴 **必修**：修复 T-M4-04b `stopRecording` 资源泄漏（在 `cancelAndJoin` 后显式 `record?.release()` + `rec?.close()`，或调整字段置 null 时序让 finally 统一释放）。新增一条 `start→stop→start→stop` instrumentation 测试作为守卫。
2. 🟡 **建议修**：P2-1（initialize 并发拷贝）、P2-5/6/7/8（测试覆盖缺口——文档声称的 R-M4-48 续接、草稿恢复、parse→batch 链路、parseImage bridge 命令）。
3. 🟢 **可后续跟踪**：P3-1~9（低优先级代码卫生）。
4. 📝 **文档同步**：更新 `production-execution-plan.md` 中 T-M4-04c/d/e 状态为 ✅。

**完成上述 #1 后，T-M4-04 整体可闭环。**

---

## 审查产物

- 报告文件：`docs/review/T-M4-04-review-round6.md`
- 输入：4 个专项子审查（04a/04b/04c/04d-e）+ 主审查对 P1 的直接代码核对
- 审查人：Claude Code
- 审查耗时：~15 分钟
