# T-M4-05 第 5 轮审查报告（P1/P2 修复复验）

- **审查日期：** 2026-09-06
- **审查范围：** 第 4 轮报告的 P1×2 + P2×4 修复复验
- **审查基线：** 工作区最新文件（`VoiceRecognitionService.kt` 481 行，`AozoraBridge.kt` 含 R-M4-43 fix，`native-bridge.test.ts` 含 voice/image 测试）
- **前置状态：** 第 4 轮结论"有条件通过（修复 P1 后通过）"
- **本轮结论：** ✅ **P1/P2 全部修复属实，审查通过**

---

## 〇、修复复验汇总

| 编号 | 优先级 | 第 4 轮问题 | 复验结果 | 证据 |
|---|---|---|---|---|
| R-M4-41 | 🔴 P1 | Vosk 模型 ~50MB 永不释放 | ✅ 已修复 | `shutdown()` 第 321-323 行始终调 `releaseModel()`；新增 `releaseModel()` 方法；`finalize()` 作 GC 安全网 |
| R-M4-42 | 🔴 P1 | 裸 CoroutineScope 不可取消 | ✅ 已修复 | `initialize(scope:)` 存储外部 scope 至 `serviceScope`；`startRecording()` 用 `scope.launch`（来自 stateLock 内捕获的 `serviceScope`）；`recognitionLoop` 的 partial 回调也改用 `serviceScope`；`shutdown()` 清 `serviceScope = null` |
| R-M4-43 | 🟡 P2 | `isRecording` TOCTOU 竞态 | ✅ 已修复 | 新增 `stateLock`；`startRecording/stopRecording/cleanup/shutdown` 全部在 `synchronized(stateLock)` 内做 check-then-set |
| R-M4-44 | 🟡 P2 | AudioRecord 异常路径未重置 | ✅ 已修复 | `startRecording()` 外层 try/catch 调 `cleanup()`；`resetAfterStartFailure()` 在 AudioRecord 构造失败后重置 `isRecording + listener`；`recognitionLoop` 加 `try/catch/finally { isRecording = false }` |
| R-M4-45 | 🟡 P2 | 无 RECORD_AUDIO 权限防御 | ✅ 已修复 | Service 层（line 135-142）+ Bridge 层（line 670-679）双重防御检查 |
| R-M4-46 | 🟡 P2 | voice/image 桥命令无单元测试 | ✅ 已修复 | `native-bridge.test.ts` 新增 13 项测试（7 voice + 6 image），全部通过 |

---

## 一、修复细节核实

### R-M4-41（P1）Vosk 模型泄漏修复

**修复方案：**
```kotlin
// shutdown() 第 321-323 行
// R-M4-41 fix：始终释放 Vosk 模型（~50MB 原生内存）
serviceScope = null
releaseModel()

// releaseModel() 第 342-347 行
fun releaseModel() {
    val m = model
    model = null
    isModelLoaded = false
    try { m?.close() } catch (_: Exception) {}
}
```

**核实结论：** ✅ 属实。`shutdown()` 在 `synchronized(stateLock)` 内捕获所有资源引用，在锁外逐一释放，避免死锁风险；`releaseModel()` 原子地清空 `model` 引用并调 `close()`；`finalize()` 作为 GC 安全网（`@Suppress("ProtectedInFinal")` 承认 Android 对 finalize 的处理）。

---

### R-M4-42（P1）裸 CoroutineScope 修复

**修复方案：**
```kotlin
// initialize() 第 68-72 行 — 存储外部 scope
fun initialize(scope: CoroutineScope, ...) {
    serviceScope = scope
    // ...用 scope.launch 加载模型...
}

// startRecording() 第 122-154 行 — 在 stateLock 内捕获 scope
val scope = synchronized(stateLock) {
    // ...checks...
    serviceScope ?: run { ... return false }
}
// 第 195 行 — 用捕获的 scope 启动识别循环
recognitionJob = scope.launch(Dispatchers.IO) { recognitionLoop(record, rec) }

// recognitionLoop 第 380-387 行 — 用 serviceScope dispatch partial
scope.launch(Dispatchers.Main) { listener?.onPartialResult(partialText) }
// scope 取消时 catch CancellationException → break
```

**核实结论：** ✅ 属实。scope 生命周期与 Activity 绑定：Activity 销毁 → scope 取消 → recognitionLoop 内 `scope.launch` 抛 `CancellationException` → break → `finally { isRecording = false }` 重置。无泄漏路径。

---

### R-M4-43（P2）TOCTOU 修复

**修复方案：**
```kotlin
// 第 51 行
private val stateLock = Any()

// startRecording() 第 122-154 行 — 所有 check-then-set 在 synchronized 内
val scope = synchronized(stateLock) {
    if (isRecording) return false
    if (!isModelLoaded) return false
    if (!hasPermission) return false
    this.listener = listener
    isRecording = true
    serviceScope ?: run { isRecording = false; this.listener = null; return false }
}
```

**核实结论：** ✅ 属实。`startRecording`、`stopRecording`、`cleanup`、`shutdown` 四处所有状态读写均在 `synchronized(stateLock)` 内，消除了竞态窗口。

---

### R-M4-44（P2）AudioRecord 异常路径修复

**修复方案：**

1. `startRecording()` 外层 catch（第 201-211 行）：`SecurityException` / 通用异常 → `cleanup()` → `return false`
2. `resetAfterStartFailure()`（第 217-222 行）：AudioRecord 构造失败后重置 `isRecording` 和 `listener`
3. `recognitionLoop`（第 357-401 行）：`try/catch/finally`
   - `catch (CancellationException)` — 正常取消
   - `catch (Exception)` — 异常时 `listener?.onError("recognition_error", ...)`
   - `finally { isRecording = false }` — 保证状态重置

**核实结论：** ✅ 属实。`finally` 块保证循环无论正常退出、协程取消还是崩溃都会重置 `isRecording`。

---

### R-M4-45（P2）RECORD_AUDIO 权限防御修复

**Service 层（VoiceRecognitionService.kt 第 135-142 行）：**
```kotlin
if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
    != PackageManager.PERMISSION_GRANTED
) {
    listener.onError("permission_denied", "...")
    return false
}
```

**Bridge 层（AozoraBridge.kt 第 670-679 行）：**
```kotlin
// R-M4-43 fix: Bridge 层防御检查 RECORD_AUDIO 权限
val ctx = syncContext
if (ctx != null) {
    val granted = ContextCompat.checkSelfPermission(...)
    if (!granted) return BridgeResponse.error(requestId, "permission_denied", ...)
}
```

**核实结论：** ✅ 属实，双重防御。Bridge 层 `if (ctx != null)` 的宽松处理是合理的：`ctx` 为 null 时 Service 层的检查仍会拦截，不会出现未检查就进入 AudioRecord 创建的路径。

---

### R-M4-46（P2）单元测试修复

**新增测试（`native-bridge.test.ts`）：**

**语音识别（7 项）：**
- `startVoiceRecording → voice.startRecording` 命令正确
- `stopVoiceRecording → voice.stopRecording` 返回转写文本
- 失败时抛出 `BridgeError code=permission_denied`
- `onVoicePartial` 监听实时部分转写
- `onVoiceFinal` 监听最终结果
- `onVoiceError` 监听对象格式错误
- `onVoiceError` 兼容旧版字符串格式（Bug-B4 回归）

**图片链路（6 项）：**
- `pickImage → image.pick` 命令，payload 含 source + quality
- `pickImage` 默认 quality=80
- `pickImage` 不设固定超时（timeoutMs=0）
- `deleteImage → image.delete` 命令，payload 含 attachmentId
- `onImageProgress` 监听上传进度
- `pickImage cancelled` 抛出 `BridgeError code=cancelled`

**核实结论：** ✅ 属实。所有 45 项 native-bridge 测试全部通过。

---

## 二、剩余 P3 问题（不阻塞通过）

| 编号 | 优先级 | 描述 | 评估 |
|---|---|---|---|
| R-M4-47 | 🟢 P3 | `stopRecording` 的 `job?.cancelAndJoin()` 无超时保护，若 Vosk native 阻塞则永远挂起 | 风险极低：`audioRecord?.stop()` 在 `cancelAndJoin()` 之前调用，`read()` 必然立即返回；实际不会触发 |
| R-M4-48 | 🟢 P3 | `finalize()` 在 Java 9+ 已弃用（Android 仍支持，@Suppress 已标注） | 纯代码风格问题，功能正确 |
| R-M4-49 | 🟢 P3 | `dispatchImageProgress` 的 `sent`/`total` Long 内联 JS，> 2^53 时精度丢失 | 实际文件大小远小于 2^53，不会触发 |
| R-M4-50 | 🟢 P3 | `ImageClient.deleteAttachment` 静默吞掉所有异常，无日志 | best-effort 设计，建议加 debug 日志 |
| R-M4-51 | 🟢 P3 | `ImageClient.buildMultipartBody` 用 `file.readBytes()` 一次性读入内存 | 压缩图通常 < 2MB，OOM 风险低；大图可用 Okio 流式替代 |
| R-M4-52 | 🟢 P3 | `ImagePicker.registerLaunchers()` KDoc 称"幂等"，但 launcher 绑定旧 Activity 有内存泄漏风险 | 实际只在 `onCreate` 调一次，建议在 KDoc 中明确说明 |

---

## 三、CI 状态

| Job | 结果 |
|---|---|
| Client Lint & Test | ✅ 通过（native-bridge.test.ts 45 项全绿） |
| Server Lint & Test | ✅ 通过 |
| Android Compile & JVM Test | ✅ 通过（3m27s） |

---

## 四、最终结论

**T-M4-05 审查通过。**

- ✅ 2 项 P1 修复属实，Vosk 内存泄漏和协程泄漏均已闭环
- ✅ 4 项 P2 修复属实，TOCTOU、AudioRecord 异常路径、权限防御、测试覆盖均已补齐
- ✅ 代码质量：VoiceRecognitionService 从 370 行增至 481 行，新增代码结构清晰，注释说明充分
- ✅ 测试覆盖：native-bridge.test.ts 从 32 项增至 45 项，voice/image 关键路径均有断言
- ✅ CI 三项全绿

**遗留挂账项（均为 P3，可随后续任务顺带处理）：**
- R-M4-47~52（见上表）
- R-M4-31~39（T-M4-05 通知/Widget/深链相关 P3 挂账项，第 3 轮登记）
- 真机复验（重启提醒、shortcut/widget/notification 导航、voice/image 全链路）

**流程问题（与第 4 轮一致，仍需跟进）：**
- 提交 `3e1607b` commit message 与实际内容不符
- T-M4-05 任务文档"数据导出与统计服务"从未实现，需澄清 re-scope
- `production-execution-plan.md` 仍标"复审不通过（第 2 轮）"，需与 `APP-CONTEXT.md` 同步

---

## 五、流程问题跟踪（2026-09-06 更新）

| 编号 | 问题 | 处置 | 状态 |
|---|---|---|---|
| FLOW-1 | `3e1607b` commit message 为"T-M4-05 数据导出与统计服务完成"，实际内容为 voice/image + R-M4-27~30 修复 | 历史 commit 无法安全改写；`production-execution-plan.md` T-M4-05 完成记录已写入 **Commit 勘误（3e1607b）** 说明（line 750），以此为准 | ✅ 已闭环 |
| FLOW-2 | T-M4-05 任务范围为"通知、快捷方式与小组件迁移"（APP-CONTEXT.md §9），"数据导出与统计服务"属 T-M2-05（已完成），从未在本任务中实现 | `APP-CONTEXT.md` §9 T-M4-05 节已补注范围澄清；`production-execution-plan.md` 勘误同步 | ✅ 已闭环 |
| FLOW-3 | `production-execution-plan.md` 曾标"复审不通过（第 2 轮）" | 顶层状态行（line 3）与子任务表（line 575）均已更新为 ✅ 通过（第 5 轮 2026-09-06）；与 APP-CONTEXT.md 同步 | ✅ 已闭环 |
| FLOW-4 | 真机复验未做（voice/image 全链路 + 通知/Widget/深链） | 已记录验证清单至 `docs/milestones/T-M4-05-device-verification-plan.md`，待真机环境执行 | ⏳ 待执行 |
