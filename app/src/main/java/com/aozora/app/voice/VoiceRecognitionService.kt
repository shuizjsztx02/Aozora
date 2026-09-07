package com.aozora.app.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import java.io.File
import java.io.FileOutputStream

/**
 * T-M4-04b: Vosk 离线语音识别服务
 *
 * 职责：
 * - 加载 Vosk 中文小模型（~50MB，存放在 assets/vosk-model-small-cn/）
 * - 管理录音生命周期（AudioRecord → Vosk Recognizer）
 * - 实时推送部分转写结果（partial）
 * - 录音结束后推送最终转写文本（final）
 *
 * 约束：
 * - 纯离线，不依赖网络
 * - 模型首次加载约 1-2 秒，后续复用
 * - 录音采样率 16000 Hz，单声道，16-bit PCM
 * - 通过回调推送转写结果，识别循环在 IO 线程
 *
 * 依赖：
 * - com.alphacephei:vosk-android:0.3.47
 * - 中文小模型（vosk-model-small-cn-0.22，约 50MB）
 */
class VoiceRecognitionService(private val context: Context) {

    companion object {
        private const val TAG = "VoiceRecognition"
        private const val MODEL_DIR = "vosk-model-small-cn"
        private const val SAMPLE_RATE = 16000
        private const val BUFFER_SIZE = 8192 // AudioRecord 缓冲区
    }

    // Vosk 模型
    @Volatile
    private var model: Model? = null
    @Volatile
    private var isModelLoaded = false

    // 录音状态
    private val stateLock = Any()
    @Volatile
    private var isRecording = false
    @Volatile
    private var serviceScope: CoroutineScope? = null
    private var audioRecord: AudioRecord? = null
    private var recognizer: Recognizer? = null
    private var recognitionJob: Job? = null

    // 回调
    @Volatile
    private var listener: VoiceRecognitionListener? = null

    /**
     * 初始化 Vosk 模型（异步，首次加载约 1-2 秒）
     *
     * Bug-B3 fix：接受外部 CoroutineScope，确保 Activity 销毁时回调不会泄漏到已销毁的 UI。
     */
    fun initialize(
        scope: CoroutineScope,
        onComplete: (success: Boolean, error: String?) -> Unit
    ) {
        serviceScope = scope
        if (isModelLoaded) {
            onComplete(true, null)
            return
        }

        scope.launch(Dispatchers.IO) {
            try {
                // 从 assets 复制模型到内部存储（Vosk 需要文件系统路径）
                val modelDir = File(context.filesDir, MODEL_DIR)
                if (!modelDir.exists() || !File(modelDir, "am/final.mdl").exists()) {
                    modelDir.deleteRecursively()
                    modelDir.mkdirs()
                    copyAssets(MODEL_DIR, modelDir)
                }

                // 加载 Vosk 模型
                val voskModel = Model(modelDir.absolutePath)
                withContext(Dispatchers.Main) {
                    model = voskModel
                    isModelLoaded = true
                    Log.i(TAG, "Vosk model loaded successfully from ${modelDir.absolutePath}")
                    onComplete(true, null)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to load Vosk model", e)
                withContext(Dispatchers.Main) {
                    onComplete(false, e.message ?: "Failed to load model")
                }
            }
        }
    }

    /**
     * 检查是否正在录音
     */
    fun isRecording(): Boolean = isRecording

    /**
     * 检查模型是否已加载
     */
    fun isModelLoaded(): Boolean = isModelLoaded

    /**
     * 开始录音和识别
     *
     * @param listener 转写结果回调
     * @return true 表示成功启动，false 表示失败（已通过 listener.onError 通知）
     */
    fun startRecording(listener: VoiceRecognitionListener): Boolean {
        val scope: CoroutineScope
        val record: AudioRecord
        val rec: Recognizer

        synchronized(stateLock) {
            if (isRecording) {
                Log.w(TAG, "Already recording")
                listener.onError("already_recording", "Already recording")
                return false
            }

            if (!isModelLoaded || model == null) {
                Log.e(TAG, "Model not loaded")
                listener.onError("model_not_loaded", "Vosk model not loaded")
                return false
            }

            // 检查 RECORD_AUDIO 权限
            if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED
            ) {
                Log.e(TAG, "RECORD_AUDIO permission not granted")
                listener.onError("permission_denied", "RECORD_AUDIO permission not granted")
                return false
            }

            val capturedScope = serviceScope
                ?: run {
                    Log.e(TAG, "Service scope not initialized — call initialize() first")
                    listener.onError("not_initialized", "Service not initialized")
                    return false
                }

            try {
                // 创建 AudioRecord（在锁内完成，避免 shutdown() 在赋值前插空）
                val bufferSize = AudioRecord.getMinBufferSize(
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT
                )
                if (bufferSize == AudioRecord.ERROR || bufferSize == AudioRecord.ERROR_BAD_VALUE) {
                    listener.onError("audio_config_error", "Invalid audio buffer size")
                    return false
                }

                val audioRec = AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    maxOf(bufferSize, BUFFER_SIZE)
                )

                if (audioRec.state != AudioRecord.STATE_INITIALIZED) {
                    audioRec.release()
                    listener.onError("audio_init_failed", "Failed to initialize AudioRecord")
                    return false
                }

                // 创建 Vosk Recognizer
                val voskRec = Recognizer(model, SAMPLE_RATE.toFloat())

                this.listener = listener
                isRecording = true
                recognizer = voskRec
                audioRecord = audioRec
                record = audioRec
                rec = voskRec
                scope = capturedScope

                // 在锁内启动协程：若 shutdown() 在此之前执行，isRecording 已为 false，
                // recognitionLoop 的 finally 会清理资源；若在此之后执行，cancel 会正常传播。
                recognitionJob = scope.launch(Dispatchers.IO) {
                    recognitionLoop(record, rec)
                }
            } catch (e: SecurityException) {
                Log.e(TAG, "Security exception starting recording", e)
                isRecording = false
                this.listener?.onError("permission_denied", e.message ?: "Permission denied")
                this.listener = null
                return false
            } catch (e: Exception) {
                Log.e(TAG, "Failed to start recording", e)
                isRecording = false
                this.listener?.onError("start_error", e.message ?: "Unknown error")
                this.listener = null
                return false
            }
        }

        // 锁外启动 AudioRecord 录音（避免长时间持锁）
        try {
            record.startRecording()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start AudioRecord", e)
            cleanup()
            listener.onError("start_error", e.message ?: "Failed to start recording")
            return false
        }

        Log.i(TAG, "Voice recording started")
        return true
    }

    /**
     * 停止录音并返回最终转写结果
     *
     * Bug-B2 fix：改为 suspend 函数，在 IO 线程等待 recognitionLoop 退出，
     * 避免 runBlocking 在主线程阻塞。
     * 先 stop() AudioRecord 解除 recognitionLoop 的 read() 阻塞，再 cancel+join job。
     *
     * @return 最终转写文本
     */
    suspend fun stopRecording(): String = withContext(Dispatchers.IO) {
        // Capture state and resources atomically under lock
        var wasRecording = false
        var job: Job? = null
        var record: AudioRecord? = null
        var rec: Recognizer? = null
        synchronized(stateLock) {
            if (isRecording) {
                wasRecording = true
                isRecording = false
                job = recognitionJob
                recognitionJob = null
                record = audioRecord
                audioRecord = null
                rec = recognizer
                recognizer = null
            }
        }

        if (!wasRecording) {
            Log.w(TAG, "Not recording")
            return@withContext ""
        }

        var finalText = ""

        try {
            // 先停止 AudioRecord，使 recognitionLoop 中的 read() 立即返回，避免阻塞
            try {
                record?.stop()
            } catch (_: Exception) { /* ignore — may already be stopped */ }

            // 取消并等待识别循环退出（在 IO 线程 join，不阻塞主线程）
            job?.cancelAndJoin()

            // 获取最终结果
            if (rec != null) {
                val finalJson = rec.finalResult
                finalText = extractText(finalJson, "text")
                Log.i(TAG, "Final transcription: $finalText")
            }

            // 释放 AudioRecord
            try {
                record?.release()
            } catch (_: Exception) { /* ignore */ }

            // 释放 Recognizer
            try {
                rec?.close()
            } catch (_: Exception) { /* ignore */ }

            listener = null
            Log.i(TAG, "Voice recording stopped")
        } catch (e: Exception) {
            Log.e(TAG, "Error stopping recording", e)
            cleanup()
        }

        finalText
    }

    /**
     * 释放所有资源，包括 Vosk 模型（~50MB 原生内存）。
     *
     * 应在 Activity.onDestroy() 或不再需要语音识别时调用。
     * 外部 serviceScope 由调用方管理（通常随 Activity 销毁而取消），
     * 此处清除引用，recognitionJob 在上方已取消。
     */
    fun shutdown() {
        val job: Job?
        val record: AudioRecord?
        val rec: Recognizer?
        synchronized(stateLock) {
            isRecording = false
            job = recognitionJob
            recognitionJob = null
            record = audioRecord
            audioRecord = null
            rec = recognizer
            recognizer = null
        }

        job?.cancel()
        try { record?.stop() } catch (_: Exception) {}
        try { record?.release() } catch (_: Exception) {}
        try { rec?.close() } catch (_: Exception) {}

        // R-M4-41 fix：始终释放 Vosk 模型（~50MB 原生内存）
        serviceScope = null
        releaseModel()
        listener = null
        Log.i(TAG, "Voice recognition service shutdown")
    }

    /**
     * R-M4-41 安全网：若 shutdown() 未被调用，GC 时释放原生模型内存。
     */
    @Suppress("ProtectedInFinal")
    protected fun finalize() {
        try {
            releaseModel()
        } catch (_: Exception) { /* ignore */ }
    }

    /**
     * 显式释放 Vosk 模型（~50MB 原生内存），不关闭整个服务。
     * 可用于内存压力时主动回收。
     */
    fun releaseModel() {
        val m: Model?
        synchronized(stateLock) {
            m = model
            model = null
            isModelLoaded = false
        }
        try { m?.close() } catch (_: Exception) {}
    }

    // ─── 内部方法 ───

    /**
     * 识别循环：从 AudioRecord 读取音频数据，喂给 Vosk Recognizer
     *
     * P2 fix：try/finally 确保循环异常退出时 isRecording 被重置，
     * 避免 AudioRecord/Recognizer 资源悬空。
     */
    private fun recognitionLoop(record: AudioRecord, rec: Recognizer) {
        val buffer = ByteArray(BUFFER_SIZE)
        // 在锁外读取一次 serviceScope，若为 null（shutdown 已执行）则直接清理并退出
        val scope: CoroutineScope = synchronized(stateLock) {
            if (!isRecording) {
                // shutdown/stop 已在协程启动前执行，本地资源需在此释放
                try { record.stop() } catch (_: Exception) {}
                try { record.release() } catch (_: Exception) {}
                try { rec.close() } catch (_: Exception) {}
                return
            }
            serviceScope ?: run {
                try { record.stop() } catch (_: Exception) {}
                try { record.release() } catch (_: Exception) {}
                try { rec.close() } catch (_: Exception) {}
                return
            }
        }

        try {
            while (isRecording && !Thread.currentThread().isInterrupted) {
                val bytesRead = record.read(buffer, 0, buffer.size)
                if (bytesRead <= 0) {
                    // 读取失败或中断
                    if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                        break
                    }
                    continue
                }

                // 喂音频数据给 Vosk
                rec.acceptWaveForm(buffer, bytesRead)
                // Bug-B1 fix：始终读取 partialResult（返回 {"partial": "..."}）。
                // 旧代码错误地在 hasResult 时读 rec.result（返回 {"text": "..."}，无 "partial" key），
                // 导致 partial 回调永远不会触发。
                val partialJson = rec.partialResult
                val partialText = extractText(partialJson, "partial")
                if (partialText.isNotBlank()) {
                    try {
                        scope.launch(Dispatchers.Main) {
                            listener?.onPartialResult(partialText)
                        }
                    } catch (_: Exception) {
                        // scope 已取消（Activity 销毁），停止循环
                        break
                    }
                }
            }
        } catch (_: CancellationException) {
            // 正常取消，无需处理
        } catch (e: Exception) {
            Log.e(TAG, "Recognition loop crashed", e)
            try {
                listener?.onError("recognition_error", e.message ?: "Recognition failed")
            } catch (_: Exception) { /* listener may throw */ }
        } finally {
            // P2 fix：无论正常退出还是崩溃，都重置 isRecording
            isRecording = false
            // 安全网：若 stopRecording/shutdown 已在协程启动前被调用并将
            // audioRecord 置 null，这里负责释放本地持有的 record。
            // 若 stopRecording 已先释放，record 是同一实例，release() 被忽略。
            synchronized(stateLock) {
                if (audioRecord === record) {
                    audioRecord = null
                }
                if (recognizer === rec) {
                    recognizer = null
                }
            }
            try { record.stop() } catch (_: Exception) {}
            try { record.release() } catch (_: Exception) {}
            try { rec.close() } catch (_: Exception) {}
        }
    }

    private fun extractText(jsonResult: String, key: String): String {
        return try {
            val obj = JSONObject(jsonResult)
            obj.optString(key, "")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to parse recognition result: $jsonResult", e)
            ""
        }
    }

    private fun cleanup() {
        val job: Job?
        val record: AudioRecord?
        val rec: Recognizer?
        synchronized(stateLock) {
            isRecording = false
            job = recognitionJob
            recognitionJob = null
            record = audioRecord
            audioRecord = null
            rec = recognizer
            recognizer = null
        }
        job?.cancel()
        try { record?.stop() } catch (_: Exception) { /* ignore */ }
        try { record?.release() } catch (_: Exception) { /* ignore */ }
        try { rec?.close() } catch (_: Exception) { /* ignore */ }
        listener = null
    }

    private fun copyAssets(assetDir: String, targetDir: File) {
        val assetManager = context.assets
        val files = assetManager.list(assetDir) ?: return

        for (filename in files) {
            val assetPath = "$assetDir/$filename"
            val targetFile = File(targetDir, filename)

            val subFiles = assetManager.list(assetPath)
            if (subFiles != null && subFiles.isNotEmpty()) {
                // 子目录
                targetFile.mkdirs()
                copyAssets(assetPath, targetFile)
            } else {
                // 文件
                targetFile.parentFile?.mkdirs()
                assetManager.open(assetPath).use { input ->
                    FileOutputStream(targetFile).use { output ->
                        input.copyTo(output)
                    }
                }
            }
        }
    }

    /**
     * 语音识别回调接口
     */
    interface VoiceRecognitionListener {
        /**
         * 部分转写结果（实时推送）
         */
        fun onPartialResult(text: String)

        /**
         * 最终转写结果（录音结束后）
         */
        fun onFinalResult(text: String)

        /**
         * 错误回调
         *
         * @param code 错误码：model_not_loaded / permission_denied / audio_init_failed / start_error / already_recording
         * @param message 错误描述
         */
        fun onError(code: String, message: String)
    }
}
