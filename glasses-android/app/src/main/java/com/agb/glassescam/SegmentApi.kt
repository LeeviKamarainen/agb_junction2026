package com.agb.glassescam

import android.util.Base64
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

/** One segmentation answer. Coordinates are pixels of the image that was sent. */
data class SegmentResult(
    val box: List<Int>,                       // [x0, y0, x1, y1], x1/y1 just past the last pixel
    val rotatedBox: List<Pair<Float, Float>>, // 4 corners of the smallest rotated rectangle
    val score: Double,                        // SAM's own quality estimate, 0..1
    val maskPng: ByteArray?,                  // black/white PNG, white = object, same size as the image
    val rawJson: String,                      // the full response, kept so it can be stored and re-parsed
)

sealed class SegmentOutcome {
    data class Ok(val result: SegmentResult) : SegmentOutcome()
    data class Fail(val message: String) : SegmentOutcome()
}

/**
 * Client for the segmentation API: POST multipart form (image, x, y) with a Bearer token.
 * Given a point on the image it returns the mask/box of the object at that point.
 * URL and token come from local.properties (segment.url, segment.token).
 */
object SegmentApi {

    val configured: Boolean get() = BuildConfig.SEGMENT_TOKEN.isNotBlank()

    suspend fun segment(
        image: ByteArray,
        x: Float,
        y: Float,
        mime: String = "image/jpeg",
    ): SegmentOutcome = withContext(Dispatchers.IO) {
        if (!configured) {
            return@withContext SegmentOutcome.Fail("Set segment.token in local.properties")
        }
        val grid = SegmentSettings.useGrid // read once so the endpoint and the form fields agree
        val boundary = "----GlassesCam${System.currentTimeMillis()}"
        val body = ByteArrayOutputStream().also { out ->
            fun text(s: String) = out.write(s.toByteArray(Charsets.UTF_8))
            fun field(name: String, value: String) {
                text("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n")
            }
            text(
                "--$boundary\r\nContent-Disposition: form-data; name=\"image\"; filename=\"frame.jpg\"\r\n" +
                    "Content-Type: $mime\r\n\r\n",
            )
            out.write(image)
            text("\r\n")
            field("x", String.format(Locale.US, "%.1f", x))
            field("y", String.format(Locale.US, "%.1f", y))
            if (grid) {
                field("grid", SegmentSettings.GRID_SIZE.toString())
                field("spacing", String.format(Locale.US, "%.2f", SegmentSettings.GRID_SPACING))
            }
            text("--$boundary--\r\n")
        }.toByteArray()

        try {
            val endpoint = if (grid) BuildConfig.SEGMENT_GRID_URL else BuildConfig.SEGMENT_URL
            val conn = (URL(endpoint).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = 10_000
                readTimeout = 60_000
                setRequestProperty("Authorization", "Bearer ${BuildConfig.SEGMENT_TOKEN}")
                setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
                setFixedLengthStreamingMode(body.size)
            }
            conn.outputStream.use { it.write(body) }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            Log.i(TAG, "segment${if (grid) "-grid" else ""} -> HTTP $code, ${text.length} chars, ${conn.getHeaderField("Server-Timing")}")
            conn.disconnect()

            if (code == 200) {
                try {
                    SegmentOutcome.Ok(parse(text))
                } catch (e: Exception) {
                    SegmentOutcome.Fail("Unreadable response: ${e.message}")
                }
            } else {
                SegmentOutcome.Fail(describeError(code, text))
            }
        } catch (e: Exception) {
            Log.w(TAG, "segment failed", e)
            SegmentOutcome.Fail("Request failed: ${e.message}")
        }
    }

    fun parse(json: String): SegmentResult {
        val o = JSONObject(json)
        val boxArr = o.getJSONArray("box")
        val box = List(boxArr.length()) { boxArr.getInt(it) }
        val rotated = o.optJSONArray("rotated_box")?.let { a ->
            List(a.length()) { i ->
                val p = a.getJSONArray(i)
                p.getDouble(0).toFloat() to p.getDouble(1).toFloat()
            }
        }.orEmpty()
        val mask = o.optString("mask_png").takeIf { it.isNotEmpty() }
            ?.let { Base64.decode(it, Base64.DEFAULT) }
        return SegmentResult(box, rotated, o.optDouble("score", 0.0), mask, json)
    }

    /** Maps the API's documented error responses to readable messages. */
    private fun describeError(code: Int, body: String): String {
        val detail: Any? = try {
            JSONObject(body).opt("detail")
        } catch (_: Exception) {
            null
        }
        return when (code) {
            401 -> "Token rejected (401): missing or wrong token"
            415 -> "Not a JPEG/PNG, or too large to decode (415)"
            422 -> when (detail) {
                is String -> detail // e.g. "point (900.0, 640.0) is outside the 720x1280 image"
                is JSONArray -> "A form field is missing or malformed: $detail"
                else -> "Rejected (422)"
            }
            else -> "HTTP $code: ${body.take(200)}"
        }
    }

    private const val TAG = "SegmentApi"
}
