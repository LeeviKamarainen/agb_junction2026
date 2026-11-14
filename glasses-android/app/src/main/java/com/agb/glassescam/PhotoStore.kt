package com.agb.glassescam

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import androidx.exifinterface.media.ExifInterface
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Saves full-resolution captures as JPEGs in the app's private storage and lists them for the gallery.
 * Files: <filesDir>/photos/IMG_yyyyMMdd_HHmmss_SSS.jpg (names sort chronologically).
 */
object PhotoStore {

    private fun dir(context: Context): File = File(context.filesDir, "photos").apply { mkdirs() }

    /** Newest first. */
    fun list(context: Context): List<File> =
        dir(context).listFiles { f -> f.extension.equals("jpg", ignoreCase = true) }
            ?.sortedByDescending { it.name }
            ?: emptyList()

    suspend fun save(context: Context, bitmap: Bitmap): File = withContext(Dispatchers.IO) {
        val stamp = SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.US).format(Date())
        val file = File(dir(context), "IMG_$stamp.jpg")
        FileOutputStream(file).use { bitmap.compress(Bitmap.CompressFormat.JPEG, 95, it) }
        file
    }

    /** Saves an already-encoded JPEG as-is (no recompression). */
    suspend fun saveJpeg(context: Context, jpeg: ByteArray): File = withContext(Dispatchers.IO) {
        val stamp = SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.US).format(Date())
        File(dir(context), "IMG_$stamp.jpg").also { it.writeBytes(jpeg) }
    }

    suspend fun delete(file: File) = withContext(Dispatchers.IO) { file.delete() }

    /** HEIC bytes from the glasses -> upright Bitmap at the original resolution. */
    fun decodeHeic(bytes: ByteArray): Bitmap? {
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null
        val orientation = try {
            ExifInterface(ByteArrayInputStream(bytes))
                .getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
        } catch (_: Exception) {
            ExifInterface.ORIENTATION_NORMAL
        }
        val degrees = when (orientation) {
            ExifInterface.ORIENTATION_ROTATE_90 -> 90f
            ExifInterface.ORIENTATION_ROTATE_180 -> 180f
            ExifInterface.ORIENTATION_ROTATE_270 -> 270f
            else -> return bitmap
        }
        val m = Matrix().apply { postRotate(degrees) }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, m, true)
    }

    /** Decodes a saved photo, downsampled so its longest side is about [maxSide] px. */
    suspend fun decode(file: File, maxSide: Int): Bitmap? = withContext(Dispatchers.IO) {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        var sample = 1
        val longest = maxOf(bounds.outWidth, bounds.outHeight)
        while (longest / (sample * 2) >= maxSide) sample *= 2
        BitmapFactory.decodeFile(file.path, BitmapFactory.Options().apply { inSampleSize = sample })
    }
}
