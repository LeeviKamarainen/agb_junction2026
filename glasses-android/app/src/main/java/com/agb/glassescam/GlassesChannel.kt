package com.agb.glassescam

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.RequestBody.Companion.toRequestBody
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.IOException
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** One authenticated socket while memory mode is armed. Never retries an uploaded JPEG: a lost
 * response might already have stored it. The camera waits for each result, so slow models cannot
 * build a queue. Text headers and their JPEGs are sent together under [sendLock]. */
class GlassesChannel(
    private val scope: CoroutineScope,
    private val baseUrl: String = BuildConfig.GLASSES_API,
    private val configuredToken: String = BuildConfig.GLASSES_TOKEN,
    private val onStatus: (String) -> Unit,
    private val onMessage: (GlassesMessage) -> Unit,
    private val client: OkHttpClient = sharedClient,
) {
    val configured: Boolean get() = baseUrl.isNotBlank()
    private val ready = MutableStateFlow<WebSocket?>(null)
    private val sendLock = Any()
    private val uploadLock = Mutex()
    private val ids = AtomicInteger(0)
    private val pending = mutableMapOf<Int, CompletableDeferred<FrameOutcome>>()
    private var job: Job? = null
    @Volatile private var socket: WebSocket? = null
    private var guestToken: String? = null
    private var guestExpires: Instant = Instant.MIN

    fun start() {
        if (job?.isActive == true) return
        if (!configured) {
            onStatus("Set glasses.api in local.properties")
            return
        }
        val previous = job
        job = scope.launch(Dispatchers.IO) {
            previous?.join() // teardown from a quick disarm/rearm finishes before opening another socket
            var backoff = 1_000L
            while (isActive) {
                var opened = false
                var detail = "Connection lost"
                try {
                    onStatus("Connecting...")
                    val token = token()
                    val closed = CompletableDeferred<String>()
                    val authenticated = CompletableDeferred<WebSocket>()
                    val ws = client.newWebSocket(
                        Request.Builder().url(endpoint("/api/glasses/ws"))
                            .header("Authorization", "Bearer $token").build(),
                        object : WebSocketListener() {
                            override fun onOpen(webSocket: WebSocket, response: Response) {
                                // The server accepts before authenticating; pong confirms the login.
                                webSocket.send("{\"type\":\"ping\",\"id\":0}")
                            }

                            override fun onMessage(webSocket: WebSocket, text: String) {
                                if (closed.isCompleted) return
                                try {
                                    val json = JSONObject(text)
                                    val id = json.optInt("id", -1)
                                    when (json.optString("type")) {
                                        "pong" -> if (id == 0) authenticated.complete(webSocket)
                                        "memory_result" -> complete(id, parseMemoryResult(json.getJSONObject("result")))
                                        "error" -> complete(id, FrameOutcome.Fail(
                                            "${json.optInt("status")}: ${json.optString("detail")}",
                                        ))
                                        "reply" -> parsePushedReply(json.getJSONObject("reply"))?.let(onMessage)
                                    }
                                } catch (_: Exception) {
                                    closed.complete("Unreadable server message")
                                    webSocket.cancel()
                                }
                            }

                            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                                if (code == 1008 && configuredToken.isBlank()) {
                                    guestToken = null
                                    guestExpires = Instant.MIN
                                }
                                closed.complete(if (code == 1008) "Login expired or rejected" else "Connection closed ($code)")
                                webSocket.close(code, null)
                            }

                            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                                closed.complete("Connection closed ($code)")
                            }

                            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                                closed.complete("Connection failed${response?.let { " (HTTP ${it.code})" } ?: ""}")
                            }
                        },
                    )
                    socket = ws
                    // A failed handshake must wake the readiness wait immediately.
                    val failure = launch { closed.await(); authenticated.cancel() }
                    try {
                        val connection = withTimeoutOrNull(10_000) { authenticated.await() }
                        if (connection != null) {
                            ready.value = connection
                            opened = true
                            onStatus("Connected")
                            detail = closed.await()
                        } else {
                            detail = "Connection timed out"
                        }
                    } catch (e: CancellationException) {
                        if (!isActive) throw e
                        detail = closed.await()
                    } finally {
                        failure.cancel()
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (_: Exception) {
                    detail = "Unable to connect; check glasses.api and login"
                } finally {
                    ready.value = null
                    socket?.cancel()
                    socket = null
                    failPending("Connection lost; frame was not retried")
                }
                if (opened) backoff = 1_000L
                onStatus("$detail; reconnecting in ${backoff / 1_000}s...")
                delay(backoff)
                backoff = (backoff * 2).coerceAtMost(30_000L)
            }
        }
    }

    fun close() {
        job?.cancel()
        ready.value = null
        socket?.cancel()
        socket = null
        failPending("Disconnected")
        onStatus("Disconnected")
    }

    suspend fun send(jpeg: ByteArray, source: String, streamId: String = ""): FrameOutcome = uploadLock.withLock {
        if (!configured) return@withLock FrameOutcome.Fail("Set glasses.api in local.properties")
        require(source == "single" || source == "stream")
        if (jpeg.size > 10 * 1024 * 1024) return@withLock FrameOutcome.Fail("Frame is larger than 10 MB")
        val ws = withTimeoutOrNull(10_000) { ready.filterNotNull().first() }
            ?: return@withLock FrameOutcome.Fail("Not connected to the backend")
        val id = ids.incrementAndGet()
        val result = CompletableDeferred<FrameOutcome>()
        val header = JSONObject().put("type", "memory_frame").put("id", id)
            .put("source", source).put("stream_id", streamId).put("seen_at", Instant.now().toString())
        try {
            synchronized(sendLock) {
                pending[id] = result
                if (!ws.send(header.toString()) || !ws.send(jpeg.toByteString())) {
                    result.complete(FrameOutcome.Fail("Connection lost; frame was not retried"))
                    ws.cancel() // never leave a header waiting for its binary payload
                }
            }
            withTimeoutOrNull(60_000) { result.await() } ?: run {
                ws.cancel()
                FrameOutcome.Fail("Frame response timed out; frame was not retried")
            }
        } finally {
            // A stopped camera may cancel this wait while the server is still busy. Reset the
            // connection so the next photo cannot collide with that unfinished upload.
            if (!result.isCompleted) ws.cancel()
            synchronized(sendLock) { pending.remove(id) }
        }
    }

    private fun complete(id: Int, result: FrameOutcome) {
        synchronized(sendLock) { pending.remove(id) }?.complete(result)
    }

    private fun failPending(detail: String) {
        val waiting = synchronized(sendLock) { pending.values.toList().also { pending.clear() } }
        waiting.forEach { it.complete(FrameOutcome.Fail(detail)) }
    }

    private fun endpoint(path: String): String {
        val url = (baseUrl.trimEnd('/') + path).toHttpUrl()
        require(url.username.isEmpty() && url.password.isEmpty() && url.query == null && url.fragment == null)
        return url.toString()
    }

    private suspend fun token(): String {
        if (configuredToken.isNotBlank()) return configuredToken
        if (guestToken != null && Instant.now().plusSeconds(10).isBefore(guestExpires)) return guestToken!!
        val request = Request.Builder().url(endpoint("/api/auth/guest"))
            .post(ByteArray(0).toRequestBody()).build()
        val call = client.newCall(request)
        val body = suspendCancellableCoroutine<String> { cont ->
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (cont.isActive) cont.resumeWithException(e)
                }
                override fun onResponse(call: Call, response: Response) {
                    response.use {
                        if (!cont.isActive) return
                        try {
                            if (it.isSuccessful) cont.resume(it.body?.string().orEmpty())
                            else cont.resumeWithException(IOException("Guest login failed (${it.code})"))
                        } catch (e: IOException) {
                            if (cont.isActive) cont.resumeWithException(e)
                        }
                    }
                }
            })
        }
        val json = JSONObject(body)
        // SQLite-backed auth responses can omit the UTC offset after a database refresh.
        val expires = json.getString("expires_at")
        guestExpires = runCatching { Instant.parse(expires) }
            .getOrElse { LocalDateTime.parse(expires).toInstant(ZoneOffset.UTC) }
        return json.getString("token").also { guestToken = it }
    }

    private companion object {
        val sharedClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .callTimeout(15, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }
}
