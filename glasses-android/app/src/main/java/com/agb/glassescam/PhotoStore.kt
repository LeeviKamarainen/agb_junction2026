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

    /**
     * Copies photos out of the app's private folder into the phone's shared storage so they show up in the phone's
     * Gallery / Google Photos / file manager and can be copied to a computer:
     *   Pictures/GlassesCam/IMG_....jpg        (the photos)
     *   Download/GlassesCam/IMG_....segment.json  (their segmentation results, if any)
     * Needs no permission (apps can add their own files through MediaStore). The originals stay in the app.
     * Exporting the same photo twice creates a second copy ("name (1).jpg"). Returns how many photos were exported.
     */
    suspend fun exportToPhone(context: Context, photos: List<File>): Int = withContext(Dispatchers.IO) {
        val resolver = context.contentResolver
        var exported = 0
        for (photo in photos) {
            val imageUri = insertAndCopy(
                resolver, photo, android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                "image/jpeg", "Pictures/GlassesCam",
            )
            if (imageUri != null) exported++
            val seg = segmentFile(photo)
            if (seg.exists()) {
                insertAndCopy(
                    resolver, seg, android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI,
                    "application/json", "Download/GlassesCam",
                )
            }
        }
        exported
    }

    private fun insertAndCopy(
        resolver: android.content.ContentResolver,
        file: File,
        collection: android.net.Uri,
        mime: String,
        relativePath: String,
    ): android.net.Uri? {
        val values = android.content.ContentValues().apply {
            put(android.provider.MediaStore.MediaColumns.DISPLAY_NAME, file.name)
            put(android.provider.MediaStore.MediaColumns.MIME_TYPE, mime)
            put(android.provider.MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
            put(android.provider.MediaStore.MediaColumns.IS_PENDING, 1) // hidden until fully written
        }
        val uri = resolver.insert(collection, values) ?: return null
        return try {
            resolver.openOutputStream(uri)?.use { out -> file.inputStream().use { it.copyTo(out) } }
            values.clear()
            values.put(android.provider.MediaStore.MediaColumns.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            uri
        } catch (e: Exception) {
            resolver.delete(uri, null, null)
            null
        }
    }

    /** Segmentation answer for a photo is stored next to it as <name>.segment.json. */
    private fun segmentFile(photo: File) = File(photo.parentFile, photo.nameWithoutExtension + ".segment.json")

    suspend fun saveSegmentJson(photo: File, json: String) =
        withContext(Dispatchers.IO) { segmentFile(photo).writeText(json) }

    suspend fun loadSegment(photo: File): SegmentResult? = withContext(Dispatchers.IO) {
        val f = segmentFile(photo)
        if (f.exists()) runCatching { SegmentApi.parse(f.readText()) }.getOrNull() else null
    }

    /**
     * Saves a copy of [photo] cropped so that the eye point ([fx], [fy], fractions of the photo) is exactly
     * the middle of the new image. That is the largest window centred on that point that stays inside the
     * photo, so the further the point is from the middle, the more of the edge is cut. Returns null if the
     * window would be tiny.
     */
    suspend fun saveEyeCrop(photo: File, fx: Float, fy: Float): File? = withContext(Dispatchers.IO) {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(photo.path, bounds)
        val w = bounds.outWidth
        val h = bounds.outHeight
        val cx = fx * w
        val cy = fy * h
        val halfW = minOf(cx, w - cx)
        val halfH = minOf(cy, h - cy)
        val rect = android.graphics.Rect(
            (cx - halfW).toInt(), (cy - halfH).toInt(), (cx + halfW).toInt(), (cy + halfH).toInt(),
        )
        if (rect.width() < 32 || rect.height() < 32) return@withContext null

        val decoder = android.graphics.BitmapRegionDecoder.newInstance(photo.path)
        try {
            val crop = decoder.decodeRegion(rect, null)
            val out = File(photo.parentFile, photo.nameWithoutExtension + "_eye.jpg")
            FileOutputStream(out).use { crop.compress(Bitmap.CompressFormat.JPEG, 95, it) }
            out
        } finally {
            decoder.recycle()
        }
    }

    /** Pixel size of a saved photo without decoding it. */
    suspend fun imageSize(photo: File): Pair<Int, Int> = withContext(Dispatchers.IO) {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(photo.path, o)
        o.outWidth to o.outHeight
    }

    suspend fun delete(file: File) = withContext(Dispatchers.IO) {
        segmentFile(file).delete()
        file.delete()
    }

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
