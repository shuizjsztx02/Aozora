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
    private var model: Model? = null
    private var isModelLoaded = false

    // 录音状态
    @Volatile
    private var isRecording = false
    private var audioRecord: AudioRecord? = null
    private var recognizer: Recognizer? = null
    private var recognitionJob: Job? = null

    // 回调
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

        this.listener = listener

        try {
            // 创建 AudioRecord
            val bufferSize = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )
            if (bufferSize == AudioRecord.ERROR || bufferSize == AudioRecord.ERROR_BAD_VALUE) {
                listener.onError("audio_config_error", "Invalid audio buffer size")
                return false
            }

            val record = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(bufferSize, BUFFER_SIZE)
            )

            if (record.state != AudioRecord.STATE_INITIALIZED) {
                record.release()
                listener.onError("audio_init_failed", "Failed to initialize AudioRecord")
                return false
            }

            // 创建 Vosk Recognizer
            val rec = Recognizer(model, SAMPLE_RATE.toFloat())
            recognizer = rec
            audioRecord = record

            // 启动录音
            record.startRecording()
            isRecording = true

            // 启动识别循环（IO 线程）
            recognitionJob = CoroutineScope(Dispatchers.IO).launch {
                recognitionLoop(record, rec)
            }

            Log.i(TAG, "Voice recording started")
            return true
        } catch (e: SecurityException) {
            Log.e(TAG, "Security exception starting recording", e)
            listener.onError("permission_denied", e.message ?: "Permission denied")
            cleanup()
            return false
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start recording", e)
            listener.onError("start_error", e.message ?: "Unknown error")
            cleanup()
            return false
        }
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
        if (!isRecording) {
            Log.w(TAG, "Not recording")
            return@withContext ""
        }

        isRecording = false
        var finalText = ""

        try {
            // 先停止 AudioRecord，使 recognitionLoop 中的 read() 立即返回，避免阻塞
            try {
                audioRecord?.stop()
            } catch (_: Exception) { /* ignore — may already be stopped */ }

            // 取消并等待识别循环退出（在 IO 线程 join，不阻塞主线程）
            recognitionJob?.cancel()
            recognitionJob?.join()
            recognitionJob = null

            // 获取最终结果
            val rec = recognizer
            if (rec != null) {
                val finalJson = rec.finalResult
                finalText = extractText(finalJson, "text")
                Log.i(TAG, "Final transcription: $finalText")
            }

            // 释放 AudioRecord
            try {
                audioRecord?.release()
            } catch (_: Exception) { /* ignore */ }
            audioRecord = null

            // 释放 Recognizer
            recognizer?.close()
            recognizer = null

            listener = null
            Log.i(TAG, "Voice recording stopped")
        } catch (e: Exception) {
            Log.e(TAG, "Error stopping recording", e)
            cleanup()
        }

        finalText
    }

    /**
     * 释放资源
     */
    fun shutdown() {
        if (isRecording) {
            isRecording = false
            recognitionJob?.cancel()
            audioRecord?.stop()
            audioRecord?.release()
            audioRecord = null
            recognizer?.close()
            recognizer = null
        }
        model?.close()
        model = null
        isModelLoaded = false
        listener = null
        Log.i(TAG, "Voice recognition service shutdown")
    }

    // ─── 内部方法 ───

    /**
     * 识别循环：从 AudioRecord 读取音频数据，喂给 Vosk Recognizer
     */
    private fun recognitionLoop(record: AudioRecord, rec: Recognizer) {
        val buffer = ByteArray(BUFFER_SIZE)

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
                CoroutineScope(Dispatchers.Main).launch {
                    listener?.onPartialResult(partialText)
                }
            }
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
        isRecording = false
        recognitionJob?.cancel()
        recognitionJob = null

        try {
            audioRecord?.stop()
        } catch (_: Exception) { /* ignore */ }
        try {
            audioRecord?.release()
        } catch (_: Exception) { /* ignore */ }
        audioRecord = null

        try {
            recognizer?.close()
        } catch (_: Exception) { /* ignore */ }
        recognizer = null

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
