package com.aozora.app.image

import android.content.Context
import android.net.Uri
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.concurrent.atomic.AtomicReference
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * T-M4-04d: Image picker — launches system camera or gallery via
 * [ActivityResultLauncher] and returns the selected image URI.
 *
 * Lifecycle:
 * - Call [registerLaunchers] from the Activity's [onCreate] BEFORE
 *   [setContentView] (ActivityResultLauncher contract).
 * - Call [pickImage] from a coroutine — it suspends until the user
 *   returns from the picker Activity.
 *
 * Cancellation:
 * If the calling coroutine is cancelled while the picker Activity is
 * open, the continuation is abandoned. The result (if the user returns)
 * is silently dropped via the `isActive` guard.
 */
class ImagePicker(private val activity: ComponentActivity) {

    /** Source for image selection. */
    enum class Source { CAMERA, GALLERY }

    private val pendingCallback = AtomicReference<((Uri?) -> Unit)?>()

    private lateinit var galleryLauncher: androidx.activity.result.ActivityResultLauncher<String>
    private lateinit var cameraLauncher: androidx.activity.result.ActivityResultLauncher<Uri>

    /**
     * Must be called from [ComponentActivity.onCreate] before [setContentView].
     * Safe to call multiple times — subsequent calls are no-ops.
     */
    fun registerLaunchers() {
        if (::galleryLauncher.isInitialized) return

        galleryLauncher = activity.registerForActivityResult(
            ActivityResultContracts.GetContent()
        ) { uri: Uri? ->
            pendingCallback.getAndSet(null)?.invoke(uri)
        }
        cameraLauncher = activity.registerForActivityResult(
            ActivityResultContracts.TakePicture()
        ) { success: Boolean ->
            // TakePicture writes to the URI we supplied; the result boolean
            // indicates whether the write succeeded.
            val cb = pendingCallback.getAndSet(null)
            if (success) {
                // The URI is the one we created in pickImage — look it up
                // from the cameraTempUri stash.
                cb?.invoke(lastCameraUri)
            } else {
                cb?.invoke(null)
            }
        }
    }

    /**
     * The last camera temp URI — set before launching the camera Activity
     * and read when the camera returns success.
     */
    @Volatile
    private var lastCameraUri: Uri? = null

    /**
     * Launch the system image picker (camera or gallery) and suspend until
     * the user returns with a result.
     *
     * @return the URI of the picked image
     * @throws PickerCancelled if the user cancelled the picker
     */
    suspend fun pickImage(source: Source): Uri = suspendCancellableCoroutine { cont ->
        val callback: (Uri?) -> Unit = { uri ->
            if (cont.isActive) {
                if (uri != null) {
                    cont.resume(uri)
                } else {
                    cont.resumeWithException(PickerCancelled())
                }
            }
        }
        // R-M4-50 fix: 防御重入——若已有 pending callback，取消旧的
        val previousCallback = pendingCallback.getAndSet(callback)
        if (previousCallback != null) {
            previousCallback(null) // 触发 PickerCancelled
        }

        when (source) {
            Source.CAMERA -> {
                val uri = createTempImageUri()
                lastCameraUri = uri
                try {
                    cameraLauncher.launch(uri)
                } catch (e: Exception) {
                    pendingCallback.set(null)
                    if (cont.isActive) {
                        cont.resumeWithException(PickerCancelled())
                    }
                }
            }
            Source.GALLERY -> {
                try {
                    galleryLauncher.launch("image/*")
                } catch (e: Exception) {
                    pendingCallback.set(null)
                    if (cont.isActive) {
                        cont.resumeWithException(PickerCancelled())
                    }
                }
            }
        }
    }

    private fun createTempImageUri(): Uri {
        // CR-01 fix: Use dedicated subdirectory to limit FileProvider exposure scope.
        val cameraDir = java.io.File(activity.cacheDir, "camera_temp").apply { mkdirs() }
        val file = java.io.File.createTempFile("aozora_cam_", ".jpg", cameraDir)
        return androidx.core.content.FileProvider.getUriForFile(
            activity,
            "${activity.packageName}.fileprovider",
            file,
        )
    }

    /** Thrown when the user cancels the image picker. */
    class PickerCancelled : Exception("Image picker cancelled")
}
