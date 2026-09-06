# T-M4-04 a/b/c 综合代码审查（第三轮）

审查日期：2026-09-06
范围：全栈——Kotlin (PermissionManager, VoiceRecognitionService, AozoraBridge, ImagePicker, ImageClient, ImageCompressor, BridgeProtocol, MainActivity) + TypeScript (bridge-types.ts, native-bridge.ts, use-voice.ts, use-image.ts, use-permissions.ts) + Python (service.py, routes.py, schemas.py, model.py, migration 0009, test_attachments.py)
审查依据：T-M4-04a/b/c 实现指南、既有两轮审查 (T-M4-04a-review.md, T-M4-04c-review.md)、验收标准

---

## 总体评价

**T-M4-04a/b/c 整体可交付，但 T-M4-04b 存在一个关键 bug（partial 转写永远不会触发），以及若干需在合并前修复的问题。**

| 子任务 | 评级 | 说明 |
|--------|------|------|
| T-M4-04a 权限状态机 | ✅ 通过 | 四态机设计稳健，SharedPreferences 持久化已修复（P2-2），桥扩展对齐 |
| T-M4-04b 语音识别 | ⚠️ 有阻塞项 | partial 转写逻辑错误（Bug-B1），stopRecording 存在 ANR 风险（Bug-B2） |
| T-M4-04c 附件上传 | ✅ 通过（有改进项） | 安全模型扎实，MIME sniff + chunked read + ownership 完整；迁移有 DDL 隐患（Bug-C1） |

---

## 🔴 关键问题（合并前必须修复）

### Bug-B1：VoiceRecognitionService.recognitionLoop — partial 转写永远不会推送

**文件：** `VoiceRecognitionService.kt:277-288`
**严重程度：** 🔴 Critical（功能失效）

```kotlin
val hasResult = rec.acceptWaveForm(buffer, bytesRead)
if (hasResult) {
    val partialJson = rec.result          // ← BUG: .result 返回最终结果（句子边界后），不是部分结果
    val partialText = extractText(partialJson, "partial")  // 最终结果 JSON 无 "partial" key
```

**问题：** Vosk API 语义——`acceptWaveForm()` 返回 `true` 表示检测到句子边界（即有了完整句子结果），此时 `rec.result` 返回的是完整句子 JSON `{"text": "..."}`，不含 `"partial"` 键。而真正的部分转写应使用 `rec.partialResult`，它返回 `{"partial": "..."}`。

当前代码的效果是：partial 回调 `onPartialResult` 永远不会被触发（因为 `extractText` 找不到 `"partial"` 键，返回空字符串，`isNotBlank()` 过滤掉了），前端 `partialText` 始终为空。只有 `finalResult`（stop 时获取）能正常工作。

**修复建议：**
```kotlin
// 方案 A：每次 acceptWaveForm 后都读取 partialResult（不管 hasResult）
rec.acceptWaveForm(buffer, bytesRead)
val partialJson = rec.partialResult
val partialText = extractText(partialJson, "partial")
if (partialText.isNotBlank()) {
    // push to listener
}

// 方案 B：hasResult 时读 result（作为 partial 的一种），同时无条件读 partialResult
if (hasResult) {
    val sentenceJson = rec.result
    val sentenceText = extractText(sentenceJson, "text")
    if (sentenceText.isNotBlank()) {
        // push as accumulated text
    }
}
val partialJson = rec.partialResult
// ...
```

---

### Bug-B2：stopRecording() 中的 runBlocking 可能导致 ANR

**文件：** `VoiceRecognitionService.kt:208`
**严重程度：** 🔴 High（UI 冻结/ANR）

```kotlin
fun stopRecording(): String {
    ...
    recognitionJob?.cancel()
    runBlocking { recognitionJob?.join() }   // ← 在主线程上阻塞等待 IO 协程结束
```

`handleVoiceStopRecording` 在 AozoraBridge 中通过 `scope.launch { dispatch(...) }` 调用，`scope` 默认是 `Dispatchers.Main`。`stopRecording()` 内部的 `runBlocking` 会阻塞主线程直到 `recognitionLoop` 退出。而 `recognitionLoop` 正在 `AudioRecord.read()` 上做阻塞 IO——该调用会在 `audioRecord.stop()` 之后才返回，但 `audioRecord.stop()` 在 `runBlocking` 之后才执行（第 220 行）。

**实际死锁风险分析：** `recognitionJob.cancel()` 设置中断标志 → `recognitionLoop` 在下一次 `read()` 返回后检查 `isInterrupted` 退出。但 `AudioRecord.read()` 是阻塞的，可能需要等到 buffer 填充。在大多数设备上这会在毫秒级完成，所以不是真正的死锁，但在极端情况（音频硬件无响应）下可冻结主线程数秒。

**修复建议：** 将 `stopRecording` 改为 `suspend` 函数，或异步化：
```kotlin
suspend fun stopRecording(): String = withContext(Dispatchers.IO) {
    recognitionJob?.cancelAndJoin()   // 在 IO 线程等待，不阻塞主线程
    ...
}
```

---

## 🟡 中等问题（建议合并前或紧随其后修复）

### Bug-B3：VoiceRecognitionService.initialize() 使用非结构化 CoroutineScope

**文件：** `VoiceRecognitionService.kt:69`
**严重程度：** 🟡 Medium

```kotlin
CoroutineScope(Dispatchers.IO).launch {
```

创建了一个没有生命周期绑定的 CoroutineScope。如果 Activity 在模型加载过程中被销毁，`withContext(Dispatchers.Main) { onComplete(...) }` 会在已销毁的 Activity 上执行回调。

**建议：** 接受一个 `CoroutineScope` 参数（类似 AozoraBridge 的做法），或使用 `lifecycleScope`。

---

### Bug-B4：Voice error 事件双重 JSON 编码

**文件：** `AozoraBridge.kt:739-753`，`native-bridge.ts:502-506`
**严重程度：** 🟡 Medium

Kotlin 侧：
```kotlin
val data = JSONObject().apply { put("code", code); put("message", message) }
// JSONObject.quote(data.toString()) → 输出 "\"{\\\"code\\\":...}\""
window.__aozoraVoiceError__(${JSONObject.quote(data.toString())});
```

JS 侧收到的参数是一个 **JSON 字符串**（被 quote 包裹的），然后 TS 做了：
```typescript
const error = typeof errorJson === 'string' ? JSON.parse(errorJson) : errorJson
```

这条路径可以工作（TS 侧做了 string → object 的 parse），但这是**不一致的**：`__aozoraVoicePartial__` 和 `__aozoraVoiceFinal__` 传的是裸字符串（text 本身），而 `__aozoraVoiceError__` 传的是 JSON 编码的字符串。对比 `__aozoraImageProgress__`，它传的是**裸对象字面量**（`{sent: $sent, total: $total}`）。

**建议：** 统一传裸对象字面量，与 imageProgress 保持一致：
```kotlin
window.__aozoraVoiceError__({code: "${code}", message: "${message.replace("\"", "\\\"")}"});
```
或更安全地通过 `JSONObject` 生成完整 JS 表达式。

---

### Bug-C1：Migration 0009 — user_id 为 nullable，无 backfill，无后续 NOT NULL 约束

**文件：** `0009_attachment_user_id.py:35-49`
**严重程度：** 🟡 Medium（数据完整性风险）

```python
op.add_column(
    "attachments",
    sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
)
# NOTE: user_id is nullable at the DDL level for ALTER TABLE simplicity.
# The ORM layer enforces NOT NULL via application logic on insert.
```

问题：
1. **现有行**（0001_initial 创建的 attachments）的 `user_id` 为 NULL，无法通过 ORM 以外的途径保证完整性
2. **无后续迁移**将 `user_id` 改为 NOT NULL（例如先 backfill 再 `ALTER COLUMN SET NOT NULL`）
3. 如果其他服务直接操作 DB（例如数据分析脚本），可以插入 `user_id = NULL` 的行

代码注释已说明这是有意为之，但需要在 TODO 中跟踪。

**建议：** 在 migration 中增加 backfill（`UPDATE attachments SET user_id = (SELECT ... FROM transactions WHERE ...)` 对已 linked 的行），然后创建后续 migration 添加 NOT NULL 约束。若当前无生产数据，可标注为 "backfill before production deploy"。

---

### Bug-A1：`permissions.getStatus` 响应含未声明字段

**文件：** `AozoraBridge.kt:568-584`，`bridge-types.ts:316-321`
**严重程度：** 🟡 Low（类型不一致）

Kotlin 侧推送：
```kotlin
put("permissions", permsObj)
put("canRecordAudio", ...)
put("canUseCamera", ...)
put("canAccessImages", ...)
```

TypeScript `PermissionStatusData` 类型：
```typescript
export interface PermissionStatusData {
  permissions: Record<PermissionType, PermissionStatus>
  // 缺少 canRecordAudio, canUseCamera, canAccessImages
}
```

`getAllStatuses()` 不包含 `POST_NOTIFICATIONS`，但 `permissionToKey()` 有映射——如果后续扩展 `getAllStatuses()` 加入 POST_NOTIFICATIONS，前端 `Record<PermissionType, ...>` 期望四个 key 但只收到三个。

**建议：** 在 `PermissionStatusData` 中声明可选的便捷字段，或移除 Kotlin 侧的冗余字段（前端可从 `permissions` record 自行派生）。

---

## 🟢 低风险 / 观察项

| 编号 | 文件 | 说明 |
|------|------|------|
| Obs-1 | `service.py:267-276` | `list_attachments` 无分页。对当前 MVP 可接受，但用户积累大量附件后会成为性能问题。建议 TODO 加 cursor pagination |
| Obs-2 | `routes.py:22-30` | 上传端点无速率限制。恶意用户可灌满磁盘。建议后续加 `slowapi` 或中间件限流 |
| Obs-3 | `service.py:243-245` | UUID collision 检查 `if dest.exists()` 理论上永远不触发（UUID4 碰撞概率极低），但若触发返回 409，客户端无法自动重试。可忽略 |
| Obs-4 | `VoiceRecognitionService.kt:323-346` | `copyAssets` 递归复制模型文件到内部存储，首次约 1-2 秒。若模型目录已存在且含 `am/final.mdl` 则跳过（正确）。但没有校验模型完整性——若复制中途崩溃，下次启动会因缺少 `am/final.mdl` 触发全量重新复制（可接受） |
| Obs-5 | `service.py:158-182` | `_mark_expired_if_needed` 在 `list_attachments` 中对每一行调用（N+1 模式）。列表大时会产生 N 次额外 DB flush。建议批量处理或改用 DB-level computed status |
| Obs-6 | `native-bridge.ts:425-518` | Voice 事件监听器链式包装（prev/wrapped）与 sync/permission/image 一致。但如果 `useVoice()` 多次挂载/卸载，全局 `window.__aozoraVoicePartial__` 链可能增长。当前 useEffect cleanup 正确解链，实际风险低 |

---

## 既有审查关闭检查

### T-M4-04a 第一轮 (N-M4-1 ~ N-M4-8)

| 编号 | 状态 | 说明 |
|------|------|------|
| N-M4-1 SharedPreferences 持久化 | ✅ 已修复 | P2-2 fix：launch 时即写标记 |
| N-M4-2 getAllStatuses 含 POST_NOTIFICATIONS | ⚠️ 部分修复 | 映射存在但 getAllStatuses() 未包含 |
| N-M4-3 权限不阻塞核心功能 | ✅ 已验证 | `isCoreFunctionBlocked()` 返回 false |
| N-M4-4 AndroidManifest 权限声明 | ✅ 已验证 | RECORD_AUDIO, CAMERA, READ_MEDIA_IMAGES, POST_NOTIFICATIONS 均声明 |
| N-M4-5 bridge 版本协商 | ✅ 已验证 | BridgeProtocol.VERSION = 6 |
| N-M4-6 PermissionResultEvent 推送 | ✅ 已验证 | JS→Kotlin→JS 链路完整 |
| N-M4-7 TS 类型对齐 | ⚠️ 部分 | PermissionStatusData 缺便捷字段（见 Bug-A1） |
| N-M4-8 测试覆盖 | ✅ 已验证 | PermissionManagerTest.kt 覆盖四态转换 |

### T-M4-04c 第一轮 (R-M4-39 ~ R-M4-42, F-M4-39 ~ F-M4-42)

| 编号 | 状态 | 说明 |
|------|------|------|
| R-M4-39 清理不删 LINKED/PARSED | ✅ 已修复 | DB-driven cleanup 精确查询 |
| R-M4-40 MIME sniff 无 header fallback | ✅ 已修复 | `_sniff_mime` 返回 None → 415 |
| R-M4-41 chunked 读取 | ✅ 已修复 | 64KB chunk 循环读取 |
| R-M4-42 过期检查覆盖全部路径 | ✅ 已修复 | `_get_owned` 中 lazy flip |
| F-M4-39 清理只删 PENDING+EXPIRED | ✅ 已修复 | WHERE 条件正确 |
| F-M4-40 sniff 失败 = 415 | ✅ 已修复 | 无 fallback |
| F-M4-41 无临时文件残留 | ✅ 已修复 | 先 read 再 write，无 SpooledTemporaryFile |
| F-M4-42 ownership 全路径强制 | ✅ 已修复 | 所有路由经 `_get_owned` |

---

## 修复优先级

1. 🔴 **Bug-B1** — partial 转写修复（VoiceRecognitionService 用 `rec.partialResult`）— **阻塞合并**
2. 🔴 **Bug-B2** — stopRecording 改为 suspend 或异步化 — **阻塞合并**
3. 🟡 **Bug-B4** — voice error 事件编码统一 — 建议合并前修复
4. 🟡 **Bug-B3** — initialize() 接受外部 CoroutineScope — 紧随其后修复
5. 🟡 **Bug-C1** — migration backfill 计划 — 生产部署前必须解决
6. 🟡 **Bug-A1** — TS 类型声明对齐 — 低优先级但应跟踪

---

## 结论

T-M4-04a 权限状态机和 T-M4-04c 附件上传经过两轮审查修复后质量扎实，安全模型（MIME sniff + magic bytes + ownership + expiry）完整可靠。**T-M4-04b 语音识别存在 partial 转写功能失效的关键 bug（Bug-B1），必须修复后才能合并。** Bug-B2（runBlocking ANR 风险）也建议在合并前处理。
