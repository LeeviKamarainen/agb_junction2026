package com.agb.glassescam

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant
import java.util.UUID

/** What a message is, so the glasses can draw them apart. Anything unknown is drawn as a reply. */
enum class MessageType { TEXT_REPLY, TRIGGER }

/**
 * A message the backend wants on the glasses screen: [MessageType.TRIGGER] when the backend noticed
 * something on its own, [MessageType.TEXT_REPLY] for an answer to the wearer.
 */
data class GlassesMessage(val type: MessageType, val title: String, val text: String)

sealed interface FrameOutcome {
    /** [stored]: the frame became a memory. [message]: show it on the glasses (null for most frames). */
    data class Ok(val stored: Boolean, val note: String?, val message: GlassesMessage?) : FrameOutcome
    data class Fail(val message: String) : FrameOutcome
}

/**
 * The backend's frame endpoint, POST <glasses.api>/api/glasses/frames (backend/app/routes/glasses.py):
 * multipart with `frame` (JPEG), `source` ("stream" or "single"), `stream_id` and `seen_at`.
 * Each frame is embedded into the vector database; the answer is usually empty, but a trigger that
 * fires returns a message to show on the glasses.
 */
object FrameApi {

    val configured: Boolean get() = BuildConfig.GLASSES_API.isNotBlank()

    private val url: String get() = BuildConfig.GLASSES_API.trimEnd('/') + "/api/glasses/frames"

    suspend fun send(jpeg: ByteArray, source: String, streamId: String = ""): FrameOutcome =
        withContext(Dispatchers.IO) {
            if (!configured) return@withContext FrameOutcome.Fail("set glasses.api in local.properties")
            try {
                val boundary = "frame-" + UUID.randomUUID()
                val body = ByteArrayOutputStream().apply {
                    fun field(name: String, value: String) {
                        write("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n".toByteArray())
                    }
                    field("source", source)
                    field("stream_id", streamId)
                    field("seen_at", Instant.now().toString())
                    write(
                        ("--$boundary\r\nContent-Disposition: form-data; name=\"frame\"; filename=\"frame.jpg\"\r\n" +
                            "Content-Type: image/jpeg\r\n\r\n").toByteArray(),
                    )
                    write(jpeg)
                    write("\r\n--$boundary--\r\n".toByteArray())
                }.toByteArray()

                val conn = (URL(url).openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    doOutput = true
                    connectTimeout = 5_000
                    readTimeout = 30_000 // a trigger that fires waits for the chat model
                    setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
                    setFixedLengthStreamingMode(body.size)
                }
                conn.outputStream.use { it.write(body) }
                val code = conn.responseCode
                val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                    ?.bufferedReader()?.use { it.readText() }.orEmpty()
                conn.disconnect()
                if (code !in 200..299) {
                    val detail = runCatching { JSONObject(text).optString("detail") }.getOrNull()
                    return@withContext FrameOutcome.Fail("HTTP $code${if (!detail.isNullOrBlank()) ": $detail" else ""}")
                }
                parse(text)
            } catch (e: Exception) {
                Log.w(TAG, "frame upload failed", e)
                FrameOutcome.Fail(e.message ?: e.javaClass.simpleName)
            }
        }

    private fun parse(text: String): FrameOutcome.Ok {
        val json = JSONObject(text)
        val message = json.optJSONObject("message")?.let {
            GlassesMessage(
                type = if (it.optString("type") == "trigger") MessageType.TRIGGER else MessageType.TEXT_REPLY,
                title = it.optString("title"),
                text = it.optString("text"),
            )
        }
        val note = if (json.isNull("note")) null else json.optString("note")
        return FrameOutcome.Ok(stored = json.optBoolean("stored"), note = note, message = message)
    }

    private const val TAG = "FrameApi"
}
