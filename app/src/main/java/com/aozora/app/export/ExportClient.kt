package com.aozora.app.export

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import com.aozora.app.auth.AuthClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine

/**
 * T-M5-04b: Export HTTP client.
 *
 * 封装服务端 /exports API 调用：创建、列表、查询、下载。
 * 下载文件通过 MediaStore 写入 Downloads/Aozora/，对系统媒体库可见。
 *
 * - Token 注入通过 [AuthClient]（token 不离开 Kotlin 层）
 * - 错误分类：AuthLost / Network / ServerError / NotFound / Expired
 */
class ExportClient(
    private val baseUrl: String,
    private val httpClient: OkHttpClient,
    private val authClient: AuthClient,
) {

    sealed class ExportError(message: String, cause: Throwable? = null) : Exception(message, cause) {
        class Network(message: String, cause: Throwable? = null) : ExportError(message, cause)
        class AuthLost(cause: Throwable? = null) : ExportError("Authentication lost", cause)
        class ServerError(val httpCode: Int, message: String) : ExportError("HTTP $httpCode: $message")
        class NotFound(message: String = "Export not found") : ExportError(message)
        class Expired(message: String = "Export has expired") : ExportError(message)
    }

    data class ExportRecord(
        val id: String,
        val bookId: String,
        val format: String,
        val status: String,
        val createdAt: String,
        val expiresAt: String?,
        val fileSize: Long?,
    )

    data class DownloadResult(
        val localPath: String,
        val filename: String,
        val content: String,
    )

    /** POST /exports — 生成导出文件。服务端同步写文件，返回即完成。 */
    suspend fun createExport(bookId: String, format: String): ExportRecord =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
                .put("book_id", bookId)
                .put("format", format)
                .toString()
            val respBody = executeAuthorized("$baseUrl/exports", "POST", body)
            parseExportRecord(JSONObject(respBody))
        }

    /** GET /exports — 列出当前用户所有导出。 */
    suspend fun listExports(): List<ExportRecord> = withContext(Dispatchers.IO) {
        val respBody = executeAuthorized("$baseUrl/exports", "GET", null)
        val arr = org.json.JSONArray(respBody)
        (0 until arr.length()).map { parseExportRecord(arr.getJSONObject(it)) }
    }

    /** GET /exports/:id — 查询单个导出。 */
    suspend fun getExport(exportId: String): ExportRecord = withContext(Dispatchers.IO) {
        val respBody = executeAuthorized("$baseUrl/exports/$exportId", "GET", null)
        parseExportRecord(JSONObject(respBody))
    }

    /**
     * GET /exports/:id/download — 下载文件，写入 Downloads/Aozora/，返回本地路径 + 内容。
     *
     * 写入策略：
     * - API 29+：通过 MediaStore.Downloads + RELATIVE_PATH="Download/Aozora" 写入
     * - 旧版：回退到 Environment.DIRECTORY_DOWNLOADS + /Aozora/ 目录
     *
     * [content] 字段供前端解析对账；文件本身也真实保存到磁盘。
     */
    suspend fun downloadExport(exportId: String, context: Context): DownloadResult =
        withContext(Dispatchers.IO) {
            val token = try {
                authClient.ensureValidAccessToken()
            } catch (e: AuthClient.AuthLostException) {
                throw ExportError.AuthLost(e)
            }

            val request = Request.Builder()
                .url("$baseUrl/exports/$exportId/download")
                .get()
                .header("Authorization", "Bearer $token")
                .header("X-Request-Id", UUID.randomUUID().toString())
                .build()

            val response = suspendSend(request)
            response.use { resp ->
                if (!resp.isSuccessful) {
                    when (resp.code) {
                        401 -> throw ExportError.AuthLost()
                        404 -> throw ExportError.NotFound()
                        410 -> throw ExportError.Expired()
                        else -> throw ExportError.ServerError(
                            resp.code,
                            resp.body?.string().orEmpty()
                        )
                    }
                }

                val contentType = resp.header("Content-Type").orEmpty()
                val ext = if (contentType.contains("csv")) "csv" else "json"
                // 从 Content-Disposition 解析文件名，回退到 export-{exportId}.{ext}
                val filename = extractFilename(resp.header("Content-Disposition"), exportId, ext)

                // 流式写入临时文件，避免全量加载到内存（大文件 OOM 防护）
                val tempFile = File.createTempFile("export-", null, context.cacheDir)
                try {
                    resp.body?.byteStream()?.use { input ->
                        tempFile.outputStream().use { output -> input.copyTo(output) }
                    }
                    // 对账用内容：只回读前 20MB，与 bridge 层截断阈值对齐
                    val readSize = minOf(tempFile.length(), MAX_CONTENT_BYTES.toLong()).toInt()
                    val contentBytes = ByteArray(readSize)
                    if (readSize > 0) {
                        tempFile.inputStream().use { it.read(contentBytes) }
                    }
                    val content = contentBytes.toString(Charsets.UTF_8)
                    // 保存完整文件到 Downloads/Aozora/
                    val localPath = saveToDownloads(context, filename, tempFile, ext)
                    DownloadResult(localPath = localPath, filename = filename, content = content)
                } finally {
                    tempFile.delete()
                }
            }
        }

    // ─── internal ────────────────────────────────────────────────────────

    private fun parseExportRecord(obj: JSONObject): ExportRecord = ExportRecord(
        id = obj.getString("id"),
        bookId = obj.getString("book_id"),
        format = obj.getString("format"),
        status = obj.getString("status"),
        createdAt = obj.getString("created_at"),
        // 空串回退而非 null：org.json 对缺失 key + null fallback 会抛 NPE
        // （Android 与 JVM 两侧都会），缺失/null/空串统一解析为 Kotlin null。
        expiresAt = obj.optString("expires_at", "").takeIf { it.isNotBlank() && it != "null" },
        fileSize = if (obj.has("file_size") && !obj.isNull("file_size"))
            obj.getLong("file_size") else null,
    )

    private suspend fun executeAuthorized(
        url: String,
        method: String,
        bodyJson: String?,
    ): String {
        val token = try {
            authClient.ensureValidAccessToken()
        } catch (e: AuthClient.AuthLostException) {
            throw ExportError.AuthLost(e)
        }

        val body = bodyJson?.toRequestBody(JSON)
        val request = Request.Builder()
            .url(url)
            .method(method, body)
            .header("Authorization", "Bearer $token")
            .header("Content-Type", "application/json")
            .header("X-Request-Id", UUID.randomUUID().toString())
            .build()

        val response = suspendSend(request)
        response.use { resp ->
            val respBody = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                when (resp.code) {
                    401 -> throw ExportError.AuthLost()
                    404 -> throw ExportError.NotFound()
                    410 -> throw ExportError.Expired()
                    else -> throw ExportError.ServerError(resp.code, respBody)
                }
            }
            return respBody
        }
    }

    private suspend fun suspendSend(request: Request) = suspendCancellableCoroutine<okhttp3.Response> { cont ->
        val call = httpClient.newCall(request)
        cont.invokeOnCancellation { call.cancel() }
        try {
            call.enqueue(object : okhttp3.Callback {
                override fun onFailure(call: okhttp3.Call, e: IOException) {
                    if (!cont.isActive) return
                    cont.resumeWithException(ExportError.Network(e.message ?: "network error", e))
                }
                override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                    if (!cont.isActive) {
                        response.close()
                        return
                    }
                    cont.resume(response)
                }
            })
        } catch (e: Exception) {
            cont.resumeWithException(ExportError.Network(e.message ?: "request failed", e))
        }
    }

    /**
     * 从 Content-Disposition 解析文件名。
     *
     * 防御：剥离路径分隔符，防止服务端返回的 filename 包含 ../ 造成路径遍历
     * （API < 29 回退到 File 写入时尤其敏感）。
     */
    private fun extractFilename(
        contentDisposition: String?,
        exportId: String,
        ext: String,
    ): String {
        if (contentDisposition != null) {
            val match = Regex("filename=\"?([^\";]+)\"?").find(contentDisposition)
            if (match != null) {
                val raw = File(match.groupValues[1]).name
                if (raw.isNotEmpty() && raw != "." && raw != "..") {
                    return raw
                }
            }
        }
        return "export-$exportId.$ext"
    }

    /**
     * 保存临时文件到 Downloads/Aozora/ 目录。
     *
     * 返回写入后的本地路径（MediaStore URI 字符串或文件路径）。
     */
    private fun saveToDownloads(
        context: Context,
        filename: String,
        tempFile: File,
        ext: String,
    ): String {
        val mimeType = if (ext == "csv") "text/csv" else "application/json"

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // API 29+: MediaStore 写入（不需要 WRITE_EXTERNAL_STORAGE）
            val resolver = context.contentResolver
            val collection = MediaStore.Downloads.getContentUri("external")
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, filename)
                put(MediaStore.Downloads.MIME_TYPE, mimeType)
                put(MediaStore.Downloads.RELATIVE_PATH, "Download/Aozora")
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = resolver.insert(collection, values)
                ?: throw ExportError.Network("Failed to create MediaStore entry")
            resolver.openOutputStream(uri)?.use { out ->
                tempFile.inputStream().use { input -> input.copyTo(out) }
            } ?: throw ExportError.Network("Failed to open output stream")
            values.clear()
            values.put(MediaStore.Downloads.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            return uri.toString()
        }

        // API < 29: 回退到文件系统（需 WRITE_EXTERNAL_STORAGE，但本 app 仅内部测试）
        val dir = Environment.getExternalStoragePublicDirectory(
            Environment.DIRECTORY_DOWNLOADS
        ).resolve("Aozora")
        if (!dir.exists()) dir.mkdirs()
        val file = dir.resolve(filename)
        tempFile.copyTo(file, overwrite = true)
        return file.absolutePath
    }

    companion object {
        private val JSON = "application/json".toMediaType()
        // 对账内容最大回读字节数，与 AozoraBridge.handleExportDownload 截断阈值对齐
        private const val MAX_CONTENT_BYTES = 20 * 1024 * 1024
    }
}
