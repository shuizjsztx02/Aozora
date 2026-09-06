# T-M4-05 第 4 轮审查报告

- **审查日期：** 2026-09-06
- **审查范围：** 提交 `3e1607b`（commit message: "feat: T-M4-05 数据导出与统计服务完成"）中的所有代码改动
- **审查基线：** HEAD `a6f5c88`
- **审查对象：** VoiceRecognitionService.kt、ImageClient.kt、ImageCompressor.kt、ImagePicker.kt、AozoraBridge.kt（voice/image handlers）、server AI 模块（fact_pack.py、prompts.py、validator.py）、test_stats_exports.py
- **前置审查说明：** 本轮为**范围校正审查**。`docs/review/T-M4-05-review.md`（第 3 轮）的审查对象是「T-M4-05a/b/c 通知、快捷方式与小组件」，但提交 `3e1607b` 实际包含的代码主要是 **T-M4-04b（语音识别）**和 **T-M4-04d（图片链路）**——从未被任何审查覆盖。本轮填补该空白。

---

## 〇、核心发现（范围错乱 + 未审查代码）

### 🔴 CRITICAL — 范围与提交归属混乱

| 现象 | 事实 |
|---|---|
| 提交 `3e1607b` message | "feat: **T-M4-05** 数据导出与统计服务完成" |
| 实际代码内容 | **T-M4-04b** VoiceRecognitionService（KDoc 第 1 行：`T-M4-04b: Vosk 离线语音识别服务`）；**T-M4-04d** ImageClient/ImageCompressor/ImagePicker（KDoc 第 1 行：`T-M4-04d`） |
| T-M4-05 任务定义（`T-M4-05-task.md`） | 通知、快捷方式与小组件（05a/05b/05c）— 已在第 3 轮审查通过 |
| 提交中"数据导出与统计服务" | `server/services/data_export.py`、`server/services/statistics.py`、`DataExportService.kt`、`StatisticsService.kt` 等文件**从未在代码库中存在** |
| 服务端实际改动 | `fact_pack.py`（balance 允许负数）、`prompts.py`（prompt 措辞调整）、`validator.py`（注释更新）、`test_stats_exports.py`（1 条断言翻转） |

**结论：** 提交 `3e1607b` 存在三项问题：
1. **跨任务代码混入**：T-M4-04b/d 代码被打包在 T-M4-05 提交中
2. **commit message 与实际内容不符**：声称"数据导出与统计服务"，实际是 voice/image
3. **T-M4-04b/d 代码从未独立审查**：VoiceRecognitionService（370 行）、ImageClient（256 行）、ImageCompressor（98 行）、ImagePicker（128 行）均为新代码，无历史审查记录

### 🔴 CRITICAL — 完整提交归属链（据探索代理核实）

| 提交 | message | 实际内容 |
|---|---|---|
| `c101afb` | "feat(T-M4-02): AI 助手安全执行链路…" | **T-M4-05 真正的核心实现**：`reminder/`、`widget/`、`shortcuts.xml`、`ReminderSettingsPage`、深链路由、`MainActivity` deep link 全部混在 T-M4-02 提交中 |
| `7228493` | "fix(ci)" | R-M4-27 部分修复（`bridge-types.ts` POST_NOTIFICATIONS + `@SuppressLint`） |
| `3e1607b` | "feat: T-M4-05 数据导出与统计服务完成" | R-M4-27~30 修复 + T-M4-04b/d voice/image 代码 + server AI 改动（42 files，无"数据导出"代码） |
| `a6f5c88` | "fix(ci)" | python-multipart + JWT warning filter，与 T-M4-05 无关 |

**完整归属问题：** T-M4-05 的核心代码（通知/Widget/深链）在 T-M4-02 的提交里；T-M4-04b/d 的代码在 T-M4-05 的提交里；`data_export.py`/`StatisticsService.kt` 从未存在过。如需按任务回滚，追溯极其困难。

---

## 一、代码质量审查（按文件）

### 1.1 VoiceRecognitionService.kt（370 行）—— 🟡 有问题

#### 🔴 P1-1：Vosk 模型永不释放（内存泄漏）

**文件：** `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt:60-97`

```kotlin
fun initialize(onComplete: ...) {
    CoroutineScope(Dispatchers.IO).launch {
        model = Model(modelDir)  // ~50MB 内存
        recognizer = Recognizer(model, SAMPLE_RATE)
        isModelLoaded = true
        onComplete(true, null)
    }
}
```

`model` 和 `recognizer` 一旦加载，**无任何 `release()` 或 `close()` 方法**，也无 lifecycle hook。应用进程期间 ~50MB 内存永久占用。Vosk `Model` 实现了 C 指针解包，不调用 `close()` 会导致 native 内存也泄漏。

**修复建议：** 增加 `fun release()` 方法，由宿主 Activity `onDestroy()` 调用；或改为懒加载 + 超时回收。

---

#### 🔴 P1-2：裸 CoroutineScope 导致初始化协程不可取消

**文件：** `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt:70`

```kotlin
CoroutineScope(Dispatchers.IO).launch {
    // 模型加载 1-2 秒，期间 Activity 销毁 → 协程泄漏
}
```

`CoroutineScope(Dispatchers.IO)` 是一个全新的、不受生命周期管理的 scope。若 Activity 在模型加载期间被销毁，协程继续执行，回调 `onComplete` 访问已销毁的 Activity 上下文。

**修复建议：** 由调用方（AozoraApp 或 ViewModel）传入 `CoroutineScope`，确保 scope 与宿主生命周期绑定。

---

#### 🟡 P2-1：`isRecording` TOCTOU 竞态

**文件：** `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt:108-128`

```kotlin
fun startRecording(listener: ...): Boolean {
    if (isRecording) return false  // check
    // ... 其他初始化 ...
    isRecording = true             // set
}
```

`isRecording` 标注了 `@Volatile`，但 `check-then-set` 复合操作非原子性。Bridge handler 在主线程调，但 `stopRecording()` 在 IO 线程（通过 `suspend` 协程），理论上存在竞态窗口（尽管实际概率低）。

**修复建议：** 改用 `AtomicBoolean` 的 `compareAndSet(false, true)`，或将 start/stop 统一到单一线程 dispatch。

---

#### 🟡 P2-2：`audioRecord` 在异常路径可能未释放

**文件：** `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt:145-210`

```kotlin
private fun startRecordingLoop() {
    audioRecord = AudioRecord(...)
    audioRecord?.startRecording()
    recognitionJob = CoroutineScope(Dispatchers.IO).launch {
        try {
            while (isRecording) {
                val buffer = ShortArray(BUFFER_SIZE)
                audioRecord?.read(buffer, 0, BUFFER_SIZE)  // 可能抛 IllegalStateException
                recognizer?.acceptWaveform(buffer, BUFFER_SIZE)  // 可能抛 native 异常
                // ...
            }
        } catch (e: Exception) {
            Log.e(TAG, "Recognition error", e)
        } finally {
            audioRecord?.stop()
            audioRecord?.release()
            audioRecord = null
        }
    }
}
```

`finally` 块只在 `recognitionJob` 的协程内执行。若协程本身被取消（`recognitionJob.cancel()` 由 `stopRecording()` 触发）之前 `audioRecord` 已被外部置 null，或 `AudioRecord.startRecording()` 抛 IllegalStateException（权限未授予时），`finally` 不会被执行到。

**更可靠的写法：** 在 `startRecording()` 入口处 `try { ... } catch { ... }` 包裹 `AudioRecord` 构造 + `startRecording()`，失败时立即释放。

---

#### 🟡 P2-3：RECORD_AUDIO 权限检查缺失

**文件：** `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt:120-130`

`startRecording()` 不检查 `RECORD_AUDIO` 权限就构造 `AudioRecord`。在权限未授予时，`AudioRecord` 构造会返回 `state == STATE_UNINITIALIZED` 的对象，后续 `startRecording()` 会抛异常。

当前实现依赖调用方（Bridge handler）提前检查，但 `VoiceRecognitionService` 本身无防御，违反"纵深防御"原则。

**修复建议：** `startRecording()` 入口处增加权限检查，无权限时返回 `false` 并回调 `onError("permission_denied", ...)`。

---

#### 🟢 P3-1：`stopRecording()` suspend 但 `recognitionJob?.join()` 可能永久挂起

**文件：** `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt:219-245`

```kotlin
suspend fun stopRecording(): String = withContext(Dispatchers.IO) {
    isRecording = false
    recognitionJob?.join()  // 等待协程结束
    // ...
}
```

若 `recognitionJob` 内部有未捕获的 native 阻塞（Vosk `acceptWaveform` 在极少数情况下可能阻塞），`join()` 会永远挂起。建议增加 `withTimeout(3000)` 包裹。

---

### 1.2 ImageClient.kt（256 行）—— 🟢 基本合格

**优点：**
- `suspendCancellableCoroutine` + `invokeOnCancellation { call.cancel() }` 正确处理协程取消
- 临时文件在 `finally { compressed.delete() }` 中清理，无泄漏
- `ProgressRequestBody` 用 8KB 分块写入，进度可观察
- HTTP 错误分类清晰（401/413/429/503 分别映射不同 error type）

**P3-2：`deleteAttachment` 静默吞掉所有异常**

```kotlin
suspend fun deleteAttachment(attachmentId: String) {
    withContext(Dispatchers.IO) {
        try {
            // ...
        } catch (_: Exception) {
            // best-effort — 忽略
        }
    }
}
```

注释说明了"best-effort"，但连网络错误也吞掉了。若服务端长期不可达，此处会静默超时（OkHttp 默认超时）。建议至少 log debug 级别。

**P3-3：`file.readBytes()` 一次性读入内存**

`buildMultipartBody` 中 `file.readBytes()` 将整个压缩后文件读入 `ByteArray`。对于最大 ~2MB 的压缩图可以接受，但若有大文件（quality=100 的原图）可能 OOM。建议用 `FileRequestBody`（Okio 流式）替代。

---

### 1.3 ImageCompressor.kt（98 行）—— ✅ 合格

- `inJustDecodeBounds` + `inSampleSize` 幂等 downsampling，内存效率正确
- `original.recycle()` 和 `scaled.recycle()` 分离处理（`scaled !== original` 判断）
- `calculateInSampleSize` 使用 power-of-two 约束，符合 Android 文档建议
- 空流 / 解码失败均有显式异常

**无问题，质量良好。**

---

### 1.4 ImagePicker.kt（128 行）—— 🟡 有小问题

**P2-4：`registerLaunchers()` 的幂等检查不充分**

```kotlin
fun registerLaunchers() {
    if (::galleryLauncher.isInitialized) return
    // ...
}
```

`ActivityResultLauncher` 必须在 Activity 的 `onCreate` 内、`setContentView` 前注册。若因某种原因 `registerLaunchers()` 在 `onCreate` 之后被调用（如配置变更后），`isInitialized` 为 true 会跳过注册，但旧 launcher 的 callback 仍指向旧 Activity 实例，导致内存泄漏或结果丢失。

实际风险低（`AozoraApp.kt` 只在 `onCreate` 调一次），但应在 KDoc 中强调"仅 onCreate 调用"而非"幂等"。

---

### 1.5 AozoraBridge.kt — Voice/Image Handlers（~210 行新增）—— 🟡 有问题

#### 🟡 P2-5：Voice/Image bridge 命令无单元测试

`BridgeProtocolTest` 当前只覆盖 6 条基础命令（`getVersion`、`ping`、`tx.*`、`book.*` 等）。T-M4-04b/d 新增的 4 条命令（`voice.startRecording`、`voice.stopRecording`、`image.pick`、`image.delete`）**完全没有测试覆盖**：

- `voice.startRecording`：`service not injected` / `model_not_loaded` / `already_recording` 三条路径均无断言
- `voice.stopRecording`：`not_recording` / 正常停止路径均无断言
- `image.pick`：`cancelled` / `compress_failed` / `timeout` / `auth_lost` / `file_too_large` / `server_error` / `network` 七条错误路径均无断言
- `image.delete`：`attachmentId blank` / 正常删除路径均无断言

这是 **T-M4-04b/d 代码的测试缺口**，也是 T-M4-05 提交混入跨任务代码导致的审查盲区。

**修复建议：** 在 `BridgeProtocolTest` 增加 voice/image 命令的 unavailable/invalid_input 路径测试（mock 各依赖）。

---

#### 🟡 P2-6：`dispatchVoiceError` / `dispatchImageProgress` 用裸 JS 对象字面量，`sent`/`total` 未 JSON 编码

**文件：** `app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt:826-840`

```kotlin
private fun dispatchImageProgress(sent: Long, total: Long) {
    val js = """
        (function(){
          ...
          window.__aozoraImageProgress__({
            sent: $sent,
            total: $total
          });
          ...
        })();
    """.trimIndent()
    webView.post { webView.evaluateJavascript(js, null) }
}
```

`sent` 和 `total` 是 `Long` 类型，直接内联进 JS 字符串。对于合理的字节数（< 2^53）没问题，但 `Long` 的最大值（9×10^18）在 JS 中会丢失精度。实际不会触发（文件大小远小于 2^53），但这是一个潜在的正确性问题，应在 KDoc 中声明上限，或改为 `JSONObject.quote(sent.toString())` 后在 JS 侧 `parseInt`。

**同样适用于 `dispatchVoicePartial`：** `text` 用 `JSONObject.quote(text)` 正确编码，无问题。

**`dispatchVoiceError`（line 744）：** `code` 和 `message` 用 `JSONObject.quote()` 正确编码，注释中的"Bug-B4 fix"说明此处已修复过。合格。

---

#### 🟢 P3-4：`handleVoiceStopRecording` 是 `suspend` 函数，但 `handleVoiceStartRecording` 不是

```kotlin
private fun handleVoiceStartRecording(requestId: String): BridgeResponse { ... }
private suspend fun handleVoiceStopRecording(requestId: String): BridgeResponse { ... }
```

`stopRecording` 是 suspend（等待 recognitionJob 结束），`startRecording` 是同步（立即返回）。这在功能上是正确的（start 异步，stop 等待结果），但分发函数 `dispatch()` 是 `suspend` 函数，`handleVoiceStartRecording` 作为普通函数被调用时会立刻返回，不会阻塞协程，行为正确。

只是代码风格不一致，建议在 `handleVoiceStartRecording` 上也加 `suspend` 关键字（即使函数体不挂起），保持所有 handler 签名一致。

---

### 1.6 Server-side changes —— ✅ 合格

| 文件 | 改动 | 评价 |
|---|---|---|
| `fact_pack.py` | `balance = income - expense`（允许负数） | ✅ 正确，负值赤字让 LLM 能生成超支洞察；与 `test_stats_exports.py` 断言翻转一致 |
| `prompts.py` | prompt 改为"指定月份"（非"本月"）；`balance` 注释改为"可能为负" | ✅ 正确，支持历史月份查询 |
| `validator.py` | 注释更新（assembly 发生在 client） | ✅ 文档准确化，无逻辑变化 |
| `test_stats_exports.py` | 测试名 `balance_non_negative` → `balance_can_be_negative`，断言 `0` → `-10000` | ✅ 与新语义一致 |

---

## 二、流程问题

### 🔴 FLOW-1：提交归属错误

提交 `3e1607b` message 写"T-M4-05"，但包含的是 T-M4-04b/d 代码。正确做法：
- T-M4-04b/d 代码应在 T-M4-04b/d 提交中
- T-M4-05 若没有自己的代码（数据导出从未实现），则不应有"feat: T-M4-05"提交

### 🟡 FLOW-2：跨任务代码混入导致审查盲区

由于 voice/image 代码被打包在 T-M4-05 提交中，T-M4-04b/d 的审查（如果存在的话）无法关联到这批代码，而 T-M4-05 的审查（第 1-3 轮）审查的是通知/Widget/深链，这批代码从未被审查过。

### 🟡 FLOW-3：T-M4-05 声称的"数据导出与统计服务"从未实现

`docs/T-M4-05-task.md` 中提到的 `server/services/data_export.py`、`StatisticsService.kt` 等文件从未创建。任务是否被 re-scoped？若是，任务文档应更新。

---

## 三、缺陷汇总

| 编号 | 优先级 | 文件 | 描述 |
|---|---|---|---|
| R-M4-41 | 🔴 P1 | VoiceRecognitionService.kt | Vosk 模型/Recognizer 无 `release()` 方法，~50MB 内存 + native 指针永久泄漏 |
| R-M4-42 | 🔴 P1 | VoiceRecognitionService.kt | `initialize()` 用裸 `CoroutineScope(Dispatchers.IO)`，Activity 销毁期间协程不可取消 |
| R-M4-43 | 🟡 P2 | VoiceRecognitionService.kt | `isRecording` TOCTOU 竞态（`@Volatile` 不足以保证 check-then-set 原子性） |
| R-M4-44 | 🟡 P2 | VoiceRecognitionService.kt | `audioRecord.release()` 在异常路径可能未执行（AudioRecord 构造失败时） |
| R-M4-45 | 🟡 P2 | VoiceRecognitionService.kt | `startRecording()` 无 RECORD_AUDIO 权限检查，依赖调用方 |
| R-M4-46 | 🟡 P2 | AozoraBridge.kt | voice/image 新增 4 条 bridge 命令无单元测试（BridgeProtocolTest 覆盖缺口） |
| R-M4-47 | 🟡 P2 | AozoraBridge.kt | `dispatchImageProgress` 的 `sent`/`total` 为 Long 直接内联 JS，> 2^53 时精度丢失 |
| R-M4-48 | 🟡 FLOW | 提交 `3e1607b` | commit message 与实际内容不符（声称 T-M4-05，实际是 T-M4-04b/d 代码） |
| R-M4-49 | 🟢 P3 | VoiceRecognitionService.kt | `stopRecording()` 的 `recognitionJob?.join()` 无超时保护 |
| R-M4-50 | 🟢 P3 | ImageClient.kt | `deleteAttachment` 静默吞掉所有异常（含网络超时），无日志 |
| R-M4-51 | 🟢 P3 | ImageClient.kt | `buildMultipartBody` 用 `file.readBytes()` 一次性读入内存，大文件有 OOM 风险 |
| R-M4-52 | 🟢 P3 | ImagePicker.kt | `registerLaunchers()` KDoc 称"幂等"，但 launcher 绑定旧 Activity 实例有内存泄漏风险 |

---

## 四、审查结论

**实现层：🟡 有条件通过（修复 P1 后通过）**

- ✅ **ImageCompressor.kt**：质量良好，无问题
- ✅ **ImageClient.kt**：HTTP 客户端逻辑正确，错误处理完整，仅有 P3 优化项
- ✅ **ImagePicker.kt**：使用现代 Activity Result API，逻辑正确
- ✅ **Server AI 模块改动**：语义一致，balance 允许负数是正确决策
- 🟡 **VoiceRecognitionService.kt**：2 项 P1（内存泄漏 + 不可取消协程），需修复后通过
- 🟡 **AozoraBridge voice/image handlers**：逻辑正确但无单元测试覆盖（P2）

**交付层：✅ 通过**

- CI 三项全绿（client lint/test、server lint/test、Android compile/JVM test）
- 前端 400 项测试通过

**流程问题（需跟进）：**

1. 提交 `3e1607b` 的 commit message 需修正（或补充说明，标注 T-M4-04b/d 代码混入）
2. T-M4-05 任务文档需更新（数据导出功能是否 re-scope？若无，任务完成状态需澄清）
3. T-M4-04b/d 的审查文档需单独建立，或在 T-M4-05 审查文档中明确标注覆盖范围

**阻塞项（修复后方可真机联调）：**

- **R-M4-41**（Vosk 内存泄漏）：必须修复，否则真机运行 30 分钟后内存压力显著
- **R-M4-42**（裸 CoroutineScope）：必须修复，否则 Activity 重建后模型加载回调访问已销毁上下文

**待复验项（与 T-M4-05 第 3 轮合并）：**

- 真机 voice 录音测试（待 R-M4-40 编译问题修复后）
- 真机 image 上传测试（待 R-M4-40 编译问题修复后）
- T-M4-05 通知/Widget/深链真机复验（第 3 轮挂账项）

---

## 五、补充说明（据探索代理核实）

### R-M4-40 编译修复状态

工作区（未提交）中 T-M4-04b/d 代码的编译问题已修复：
- `VoiceRecognitionService.kt:277` `acceptWaveform` → `acceptWaveForm`（正确 Vosk API）
- `stopRecording()` 改为 `suspend` 函数，AudioRecord 先 stop 再 join
- `AozoraBridge.kt` 双重 JSON 编码 bug 修复（`dispatchVoiceError` 改用裸对象字面量）

修复属实，但**仍未提交**。建议尽快提交后重跑 Kotlin 单测（34 项 T-M4-05 测试 + T-M4-04b/d 测试）。

### 文档状态不一致

- `production-execution-plan.md` 仍标注 T-M4-05 "⚠️ 复审不通过（第 2 轮）"——但第 3 轮已改为实现层通过，文档未同步更新
- `APP-CONTEXT.md` §3.1 已写 "T-M4-05 ✅"——与 plan 文档不一致

### Kotlin 单测尚未在 HEAD 上全绿

第 3 轮审查时因 R-M4-40 阻塞，Kotlin 测试未能执行。工作区修复后尚未提交，因此**T-M4-05 的 Kotlin 单测（ReminderSchedulerTest 9 / WidgetDataHelperTest 15 / AozoraWidgetProviderTest 4 / BridgeProtocolTest 6）从未在提交后全绿**。这是一个交付层缺口，需提交后复跑确认。
