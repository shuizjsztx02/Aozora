package com.aozora.app.image

import com.aozora.app.auth.AuthClient
import com.aozora.app.image.ImageCompressor.ImageCompressException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InterruptedIOException
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * T-M4-04d: Image upload HTTP client.
 *
 * Compresses a content/file URI to JPEG and POSTs it as a multipart upload
 * to ``POST /attachments/upload`` on the app server.
 *
 * - Token injection via [AuthClient] (token never leaves Kotlin layer)
 * - Upload progress tracked via OkHttp counting sink → JS bridge event
 * - Compressed temp file cleaned up on both success and error
 */
class ImageClient(
    private val baseUrl: String,
    private val httpClient: OkHttpClient,
    private val authClient: AuthClient,
) {

    sealed class ImageError(message: String, cause: Throwable? = null) : Exception(message, cause) {
        class CompressFailed(cause: Throwable) : ImageError("Image compression failed", cause)
        class Network(message: String, cause: Throwable? = null) : ImageError(message, cause)
        class Timeout(cause: Throwable? = null) : ImageError("Upload timed out", cause)
        class AuthLost(cause: Throwable? = null) : ImageError("Authentication lost", cause)
        class ServerError(val httpCode: Int, message: String) : ImageError("HTTP $httpCode: $message")
        class FileTooLarge(size: Long, limit: Long) :
            ImageError("File size $size exceeds limit $limit")
    }

    data class UploadResult(
        val attachmentId: String,
        val mimeType: String,
        val fileSize: Long,
    )

    /**
     * Compress the image at [imageUri] and upload to the server.
     *
     * Progress events (bytes written / total) are reported through [onProgress].
     * The compressed temp file is always cleaned up before returning.
     */
    suspend fun uploadImage(
        imageUri: String,
        context: android.content.Context,
        quality: Int = 80,
        onProgress: (sent: Long, total: Long) -> Unit,
    ): UploadResult = withContext(Dispatchers.IO) {
        // --- 1. Open input stream from URI
        val input = try {
            val uri = android.net.Uri.parse(imageUri)
            context.contentResolver.openInputStream(uri)
                ?: java.io.FileInputStream(File(uri.path ?: imageUri))
        } catch (e: Exception) {
            throw ImageError.Network("Cannot open image: ${e.message}", e)
        }

        // --- 2. Compress to temp JPEG
        val compressed = try {
            ImageCompressor.compress(input, context.cacheDir, quality)
        } catch (e: ImageCompressException) {
            throw ImageError.CompressFailed(e)
        } finally {
            input.close()
        }

        // --- 3. Upload (cleanup temp file on both success and error)
        try {
            return@withContext doUpload(compressed, onProgress)
        } catch (e: ImageError) {
            throw e
        } catch (e: Exception) {
            throw ImageError.Network("Upload failed: ${e.message}", e)
        } finally {
            compressed.delete()
        }
    }

    /**
     * Best-effort delete of a server-side attachment.
     *
     * Used by the frontend to clean up a draft attachment that the user
     * chose to discard. Failures are silently ignored (server will
     * auto-expire PENDING attachments after 24h).
     */
    suspend fun deleteAttachment(attachmentId: String) {
        withContext(Dispatchers.IO) {
            try {
                val token = try {
                    authClient.ensureValidAccessToken()
                } catch (e: AuthClient.AuthLostException) {
                    return@withContext // auth lost — server will clean up
                }
                val request = Request.Builder()
                    .url("$baseUrl/attachments/$attachmentId")
                    .delete()
                    .header("Authorization", "Bearer $token")
                    .build()
                httpClient.newCall(request).execute().use { /* consume response */ }
            } catch (_: Exception) {
                // best-effort — ignore
            }
        }
    }

    // ─── internal ────────────────────────────────────────────────────────

    private suspend fun doUpload(
        file: File,
        onProgress: (Long, Long) -> Unit,
    ): UploadResult {
        val token = try {
            authClient.ensureValidAccessToken()
        } catch (e: AuthClient.AuthLostException) {
            throw ImageError.AuthLost(e)
        }

        val body = buildMultipartBody(file, onProgress)

        val request = Request.Builder()
            .url("$baseUrl/attachments/upload")
            .post(body)
            .header("Authorization", "Bearer $token")
            .header("X-Request-Id", UUID.randomUUID().toString())
            .build()

        val response = suspendSend(request)

        response.use { resp ->
            val respBody = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                when (resp.code) {
                    401 -> throw ImageError.AuthLost()
                    413 -> throw ImageError.FileTooLarge(file.length(), 10 * 1024 * 1024)
                    429, 503 -> throw ImageError.ServerError(resp.code, respBody)
                    else -> throw ImageError.Network(
                        serverMessage(respBody, resp.code)
                    )
                }
            }
            return parseAttachmentResponse(respBody)
        }
    }

    private fun buildMultipartBody(
        file: File,
        onProgress: (Long, Long) -> Unit,
    ): MultipartBody {
        val bytes = file.readBytes()
        val contentType = guessMime(file)

        return MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addPart(
                MultipartBody.Part.createFormData(
                    "file",
                    "image.jpg",
                    ProgressRequestBody(bytes, contentType.toMediaType(), onProgress),
                )
            )
            .build()
    }

    private fun guessMime(file: File): String {
        val ext = file.extension.lowercase()
        return when (ext) {
            "jpg", "jpeg" -> "image/jpeg"
            "png" -> "image/png"
            "webp" -> "image/webp"
            else -> "image/jpeg"
        }
    }

    private fun parseAttachmentResponse(body: String): UploadResult {
        val json = try { JSONObject(body) } catch (e: Exception) {
            throw ImageError.Network("Invalid server response: ${e.message}", e)
        }
        return UploadResult(
            attachmentId = json.getString("id"),
            mimeType = json.getString("mime_type"),
            fileSize = json.optLong("file_size", 0),
        )
    }

    private suspend fun suspendSend(request: Request): Response =
        kotlinx.coroutines.suspendCancellableCoroutine { cont ->
            val call = httpClient.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (!cont.isActive) return
                    val error = when (e) {
                        is InterruptedIOException -> ImageError.Timeout(e)
                        else -> ImageError.Network(e.message ?: "Network error", e)
                    }
                    cont.resumeWithException(error)
                }

                override fun onResponse(call: Call, response: Response) {
                    if (!cont.isActive) {
                        response.close()
                        return
                    }
                    cont.resume(response)
                }
            })
        }

    private fun serverMessage(body: String, code: Int): String {
        val msg = try { JSONObject(body).optString("message", "") } catch (_: Exception) { "" }
        return if (msg.isNotBlank()) "Server error: HTTP $code: $msg" else "Server error: HTTP $code"
    }

    // ─── Progress-tracking request body ───

    private class ProgressRequestBody(
        private val bytes: ByteArray,
        private val contentType: okhttp3.MediaType,
        private val onProgress: (Long, Long) -> Unit,
    ) : okhttp3.RequestBody() {
        override fun contentType() = contentType
        override fun contentLength() = bytes.size.toLong()

        override fun writeTo(sink: okio.BufferedSink) {
            val total = bytes.size.toLong()
            // Write in 8 KB chunks so progress is observable for large images.
            val chunk = 8192L
            var offset = 0L
            while (offset < total) {
                val end = minOf(offset + chunk, total)
                sink.write(bytes, offset.toInt(), (end - offset).toInt())
                offset = end
                onProgress(offset, total)
            }
            sink.flush()
        }
    }
}
