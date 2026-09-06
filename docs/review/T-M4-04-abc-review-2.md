# T-M4-04 a/b/c 修复验证审查（第四轮）

审查日期：2026-09-06
范围：验证第三轮审查发现的 6 个问题（Bug-B1 ~ B4, Bug-C1, Bug-A1）的修复
结论：**全部通过，可合并。**

---

## 逐项验证

### ✅ Bug-B1：partial 转写修复 — **已修复**

**文件：** `VoiceRecognitionService.kt:292-302`

旧代码在 `acceptWaveForm()` 返回 true 时读 `rec.result`（返回 `{"text": "..."}`，无 `"partial"` 键），导致 partial 回调永远不触发。

修复后：
```kotlin
rec.acceptWaveForm(buffer, bytesRead)
val partialJson = rec.partialResult       // ← 正确：始终读取 partialResult
val partialText = extractText(partialJson, "partial")
if (partialText.isNotBlank()) {
    CoroutineScope(Dispatchers.Main).launch {
        listener?.onPartialResult(partialText)
    }
}
```

验证：
- `acceptWaveForm` 返回值被忽略（正确：partial 结果在每次喂数据后都应检查，不等句子边界）
- `rec.partialResult` 返回 `{"partial": "..."}` 格式，`extractText(..., "partial")` 正确提取
- 非空过滤 `isNotBlank()` 避免推送空字符串

**结论：** 修复正确，partial 转写链路现在可以正常工作。

---

### ✅ Bug-B2：stopRecording suspend 化 — **已修复**

**文件：** `VoiceRecognitionService.kt:205-251`，`AozoraBridge.kt:690`

旧代码 `runBlocking { recognitionJob?.join() }` 在主线程阻塞等待 IO 协程。

修复后：
```kotlin
suspend fun stopRecording(): String = withContext(Dispatchers.IO) {
    ...
    try { audioRecord?.stop() } catch (_: Exception) { }  // 先 stop，解除 read() 阻塞
    recognitionJob?.cancel()
    recognitionJob?.join()                                  // 在 IO 线程 join，不阻塞主线程
    ...
    val finalJson = rec.finalResult
    ...
}
```

验证：
- `withContext(Dispatchers.IO)` 确保整个 stop 流程在 IO 线程执行
- `audioRecord.stop()` 在 `cancel()+join()` 之前，确保 `record.read()` 立即返回，recognitionLoop 退出
- `handleVoiceStopRecording` 改为 `suspend fun`，从 `suspend fun dispatch` 调用，协程链路完整
- 资源释放顺序正确：stop AudioRecord → join job → 读 finalResult → release AudioRecord → close Recognizer

**结论：** 修复正确，不再阻塞主线程。stop→cancel→join 顺序确保 recognitionLoop 及时退出。

---

### ✅ Bug-B3：initialize 接受外部 CoroutineScope — **已修复**

**文件：** `VoiceRecognitionService.kt:65-68`，`MainActivity.kt:106`

旧代码创建 `CoroutineScope(Dispatchers.IO).launch { ... }` 无生命周期绑定。

修复后：
```kotlin
fun initialize(
    scope: CoroutineScope,    // ← 新增参数
    onComplete: (success: Boolean, error: String?) -> Unit
) { ... }
```

MainActivity 传入自己的 `scope`：
```kotlin
app.voiceRecognitionService.initialize(scope) { success, error -> ... }
```

**结论：** Activity 销毁时 `scope.cancel()` 会取消加载协程，防止回调泄漏。

---

### ✅ Bug-B4：voice error 事件编码统一 — **已修复**

**文件：** `AozoraBridge.kt:744-756`，`native-bridge.ts:501-519`

旧代码用 `JSONObject.quote(data.toString())` 双重编码，JS 收到字符串而非对象。

Kotlin 修复后：
```kotlin
val safeCode = JSONObject.quote(code)        // "\"permission_denied\""
val safeMessage = JSONObject.quote(message)  // "\"RECORD_AUDIO permission not granted\""
window.__aozoraVoiceError__({code: $safeCode, message: $safeMessage});
// → JS 收到裸对象 {code: "permission_denied", message: "..."}
```

TypeScript 修复后：
```typescript
const wrapped = (errorOrJson: { code: string; message: string } | string) => {
  const error = typeof errorOrJson === 'string' ? JSON.parse(errorOrJson) : errorOrJson
  ...
}
```

验证：
- `JSONObject.quote()` 对每个字段单独转义，生成的 JS 对象字面量安全
- TS 侧保留 `typeof === 'string'` 分支以兼容旧版 Android（优雅降级）
- 与 `dispatchImageProgress`（裸对象字面量）风格一致

**结论：** 修复正确，双重编码消除，向后兼容。

---

### ✅ Bug-C1：Migration 0009 backfill — **已确认 TODO**

**文件：** `0009_attachment_user_id.py:51-57`

修复：添加了明确的 TODO 注释，列出生产部署前的三步操作：
1. Backfill `user_id`（从 `transactions` 表关联）
2. 创建 0010 migration 添加 NOT NULL
3. 清理无 `user_id` 的孤立 PENDING 行

**结论：** 当前无生产数据，TODO 标注清晰，可接受。生产部署前需执行。

---

### ✅ Bug-A1：PermissionStatusData 类型对齐 — **已修复**

**文件：** `bridge-types.ts:316-321`

```typescript
export interface PermissionStatusData {
  permissions: Record<PermissionType, PermissionStatus>
  canRecordAudio: boolean       // ← 新增
  canUseCamera: boolean         // ← 新增
  canAccessImages: boolean      // ← 新增
}
```

**结论：** 类型声明与 Kotlin 侧响应字段完全对齐。

---

## 新增回归检查

| 检查项 | 结果 |
|--------|------|
| `stopRecording` suspend 化后，`dispatch` 调用链是否完整？ | ✅ `dispatch` 本身是 `suspend fun`，调用 `suspend handleVoiceStopRecording` 无问题 |
| `audioRecord.stop()` → `cancel()` → `join()` 顺序是否安全？ | ✅ stop 使 `record.read()` 立即返回 → loop 退出 → join 成功 |
| `rec.partialResult` 在 recognitionLoop 中调用是否与 `rec.finalResult`（stopRecording 中）存在并发？ | ✅ `join()` 确保 recognitionLoop 退出后才访问 `finalResult` |
| `dispatchVoiceError` 新写法是否有 XSS 注入风险？ | ✅ `JSONObject.quote()` 转义 `"`、`\`、控制字符 |
| `initialize(scope)` 的 `scope` 在 Activity 销毁时是否被取消？ | ✅ MainActivity 的 `scope` 绑定 Activity 生命周期 |
| `PermissionStatusData` 新字段是否破坏现有前端代码？ | ✅ 新增字段是 additive，不改变 `permissions` record 结构 |

---

## 残留观察（非阻塞）

| 编号 | 文件 | 说明 |
|------|------|------|
| R-1 | `VoiceRecognitionService.kt:177` | `startRecording` 内部仍创建 `CoroutineScope(Dispatchers.IO).launch { recognitionLoop(...) }`，未绑定生命周期。若 Activity 销毁时录音仍在进行，该协程泄漏。建议后续将 `recognitionJob` 的 scope 也外部化，或在 `shutdown()` 中确保 join |
| R-2 | `AozoraBridge.kt:568-584` | `getAllStatuses()` 不含 POST_NOTIFICATIONS，但 `PermissionType` 有四项。前端 `Record<PermissionType, PermissionStatus>` 期望四键但只收到三键。不影响运行时（TS Record 不检查 key 完整性），但类型语义不精确 |
| R-3 | `service.py:267-276` | `list_attachments` 无分页（同第三轮） |

---

## 结论

第三轮审查发现的 6 个问题全部修复正确，无新增回归。

| 子任务 | 评级 | 说明 |
|--------|------|------|
| T-M4-04a 权限状态机 | ✅ 通过 | 类型对齐已修复 |
| T-M4-04b 语音识别 | ✅ 通过 | partial 转写、suspend stop、scope 注入、error 编码全部修复 |
| T-M4-04c 附件上传 | ✅ 通过 | migration TODO 已标注 |

**可合并。** 残留观察项（R-1 ~ R-3）为非阻塞改进，可后续处理。
