package com.agb.glassescam

import android.graphics.Bitmap
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL

/**
 * Where photos captured from the glasses go. This is the hook for the hackathon backend:
 * set backend.url in local.properties and the JPEG is POSTed there (Content-Type: image/jpeg).
 * With no URL configured it only logs.
 */
object PhotoSink {

    suspend fun send(bitmap: Bitmap): String = withContext(Dispatchers.IO) {
        val jpeg = ByteArrayOutputStream().also { bitmap.compress(Bitmap.CompressFormat.JPEG, 90, it) }
            .toByteArray()

        val url = BuildConfig.BACKEND_URL
        if (url.isBlank()) {
            Log.i(TAG, "No backend.url set; captured ${jpeg.size} bytes (${bitmap.width}x${bitmap.height})")
            return@withContext "Captured ${bitmap.width}x${bitmap.height} (${jpeg.size / 1024} KB), no backend set"
        }

        try {
            val conn = (URL(url).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = 5_000
                readTimeout = 15_000
                setRequestProperty("Content-Type", "image/jpeg")
            }
            conn.outputStream.use { it.write(jpeg) }
            val code = conn.responseCode
            conn.disconnect()
            "Uploaded ${jpeg.size / 1024} KB -> HTTP $code"
        } catch (e: Exception) {
            Log.w(TAG, "upload failed", e)
            "Upload failed: ${e.message}"
        }
    }

    private const val TAG = "PhotoSink"
}
