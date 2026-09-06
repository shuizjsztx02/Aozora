package com.aozora.app.image

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream

/**
 * T-M4-04d: Client-side image compression.
 *
 * Pipeline:
 * 1. Decode input to Bitmap (with [inSampleSize](https://developer.android.com/topic/performance/graphics/load-bitmap) pre-decode)
 * 2. Scale so longest edge ≤ maxDimension
 * 3. Encode to JPEG at quality
 *
 * The implementation uses only Android framework classes that Robolectric
 * shadows in JVM unit tests, so compression logic is fully unit-testable.
 */
object ImageCompressor {

    /**
     * Compress an image [InputStream] to a temporary JPEG file in [cacheDir].
     *
     * @return the compressed file (caller is responsible for cleanup)
     * @throws ImageCompressException when decoding fails or output is empty
     */
    fun compress(
        input: InputStream,
        cacheDir: File,
        quality: Int = 80,
        maxDimension: Int = 2048,
    ): File {
        val bytes = input.readBytes()
        if (bytes.isEmpty()) {
            throw ImageCompressException("Input stream is empty")
        }

        // --- bounds-only decode to compute sample size without allocating the full bitmap
        val boundsOpts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeStream(ByteArrayInputStream(bytes), null, boundsOpts)
        val origW = boundsOpts.outWidth
        val origH = boundsOpts.outHeight
        if (origW <= 0 || origH <= 0) {
            throw ImageCompressException("Unable to decode image dimensions")
        }

        // --- pre-decode downsampling (power-of-two for efficiency)
        val decodeOpts = BitmapFactory.Options().apply {
            inSampleSize = calculateInSampleSize(origW, origH, maxDimension)
        }
        val original = BitmapFactory.decodeStream(ByteArrayInputStream(bytes), null, decodeOpts)
            ?: throw ImageCompressException("Failed to decode image")

        // --- proportional scale so longest edge ≤ maxDimension
        val scaled = scaleIfNeeded(original, maxDimension)
        if (scaled !== original) original.recycle()

        try {
            val baos = ByteArrayOutputStream()
            val ok = scaled.compress(Bitmap.CompressFormat.JPEG, quality, baos)
            if (!ok || baos.size() == 0) {
                throw ImageCompressException("JPEG compression produced no output")
            }
            val outFile = File.createTempFile("aozora_img_", ".jpg", cacheDir)
            outFile.writeBytes(baos.toByteArray())
            return outFile
        } finally {
            scaled.recycle()
        }
    }

    /**
     * Returns an inSampleSize ≥ 1 that keeps the decoded bitmap within
     * twice the target dimension (power-of-two constraint of the decoder).
     */
    internal fun calculateInSampleSize(width: Int, height: Int, maxDim: Int): Int {
        var sample = 1
        val halfW = width / 2
        val halfH = height / 2
        while (halfW / sample >= maxDim && halfH / sample >= maxDim) {
            sample *= 2
        }
        return sample
    }

    /** Returns [bitmap] unchanged if already within bounds; a fresh scaled copy otherwise. */
    private fun scaleIfNeeded(bitmap: Bitmap, maxDim: Int): Bitmap {
        val w = bitmap.width
        val h = bitmap.height
        if (w <= maxDim && h <= maxDim) return bitmap
        val scale = maxDim.toFloat() / maxOf(w, h)
        return Bitmap.createScaledBitmap(bitmap, (w * scale).toInt(), (h * scale).toInt(), true)
    }

    class ImageCompressException(message: String) : Exception(message)
}
