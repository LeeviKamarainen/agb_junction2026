package com.agb.glassescam

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.ByteString
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class GlassesChannelTest {
    private val server = MockWebServer()
    private val client = OkHttpClient()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val statuses = LinkedBlockingQueue<String>()
    private val messages = LinkedBlockingQueue<GlassesMessage>()
    private val headers = LinkedBlockingQueue<JSONObject>()
    private val images = LinkedBlockingQueue<ByteArray>()
    private lateinit var channel: GlassesChannel

    @Before fun setup() { server.start() }

    @After fun cleanup() {
        if (::channel.isInitialized) channel.close()
        scope.cancel()
        client.dispatcher.executorService.shutdownNow()
        client.connectionPool.evictAll()
        server.shutdown()
    }

    private fun connect(token: String = "app-login") {
        channel = GlassesChannel(
            scope, server.url("/").toString(), token, statuses::offer, messages::offer, client,
        )
        channel.start()
        awaitStatus("Connected")
    }

    private fun awaitStatus(expected: String) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(8)
        while (System.nanoTime() < deadline) {
            if (statuses.poll(100, TimeUnit.MILLISECONDS) == expected) return
        }
        fail("Never received status: $expected")
    }

    private fun socket(onImage: (WebSocket, Int) -> Unit = { ws, id -> result(ws, id) }) {
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            private var id = -1
            override fun onMessage(webSocket: WebSocket, text: String) {
                val json = JSONObject(text)
                if (json.getString("type") == "ping") {
                    webSocket.send("{\"type\":\"pong\",\"id\":0}")
                } else {
                    id = json.getInt("id")
                    headers.offer(json)
                }
            }
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                images.offer(bytes.toByteArray())
                onImage(webSocket, id)
            }
        }))
    }

    private fun result(ws: WebSocket, id: Int) {
        ws.send("""{"type":"memory_result","id":$id,"result":{"stored":true,"message":{"type":"trigger","title":"Keys","text":"Take your keys"}}}""")
    }

    @Test fun `sends authenticated paired header and jpeg and parses trigger result`() = runBlocking {
        socket()
        connect()
        val jpeg = byteArrayOf(1, 2, 3)
        val out = withTimeout(5_000) { channel.send(jpeg, "stream", "phone-stream") } as FrameOutcome.Ok
        val request = server.takeRequest(5, TimeUnit.SECONDS)!!
        assertEquals("/api/glasses/ws", request.path)
        assertEquals("Bearer app-login", request.getHeader("Authorization"))
        val header = headers.poll(5, TimeUnit.SECONDS)!!
        assertEquals("memory_frame", header.getString("type"))
        assertEquals("stream", header.getString("source"))
        assertEquals("phone-stream", header.getString("stream_id"))
        assertTrue(header.getString("seen_at").endsWith("Z"))
        assertArrayEquals(jpeg, images.poll(5, TimeUnit.SECONDS))
        assertTrue(out.stored)
        assertEquals(MessageType.TRIGGER, out.message!!.type)
        assertEquals("Take your keys", out.message!!.text)
    }

    @Test fun `guest login happens before socket and token is kept out of URL`() {
        server.enqueue(MockResponse().setResponseCode(201).setBody(
            """{"token":"temporary-login","expires_at":"2099-01-01T00:00:00"}""",
        ))
        socket()
        connect(token = "")
        assertEquals("/api/auth/guest", server.takeRequest(5, TimeUnit.SECONDS)!!.path)
        val request = server.takeRequest(5, TimeUnit.SECONDS)!!
        assertEquals("/api/glasses/ws", request.path)
        assertEquals("Bearer temporary-login", request.getHeader("Authorization"))
    }

    @Test fun `request errors leave connection usable`() = runBlocking {
        var count = 0
        socket { ws, id ->
            if (++count == 1) ws.send("""{"type":"error","id":$id,"status":503,"detail":"No embedding model"}""")
            else result(ws, id)
        }
        connect()
        val failed = withTimeout(5_000) { channel.send(byteArrayOf(1), "single") } as FrameOutcome.Fail
        assertTrue(failed.message.contains("503"))
        assertTrue(withTimeout(5_000) { channel.send(byteArrayOf(2), "single") } is FrameOutcome.Ok)
    }

    @Test fun `quick disarm and rearm reuse guest login and open a fresh connection`() {
        server.enqueue(MockResponse().setResponseCode(201).setBody(
            """{"token":"temporary-login","expires_at":"2099-01-01T00:00:00Z"}""",
        ))
        socket()
        socket()
        connect(token = "")
        channel.close()
        channel.start()
        awaitStatus("Connected")
        assertEquals("/api/auth/guest", server.takeRequest(5, TimeUnit.SECONDS)!!.path)
        repeat(2) {
            val request = server.takeRequest(5, TimeUnit.SECONDS)!!
            assertEquals("/api/glasses/ws", request.path)
            assertEquals("Bearer temporary-login", request.getHeader("Authorization"))
        }
        assertEquals(3, server.requestCount)
    }

    @Test fun `a refused guest is replaced before reconnecting`() {
        fun guest(token: String) = MockResponse().setResponseCode(201).setBody(
            """{"token":"$token","expires_at":"2099-01-01T00:00:00Z"}""",
        )
        server.enqueue(guest("expired"))
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.close(1008, "login expired")
            }
        }))
        server.enqueue(guest("fresh"))
        socket()
        connect(token = "")
        assertEquals("/api/auth/guest", server.takeRequest(5, TimeUnit.SECONDS)!!.path)
        assertEquals("Bearer expired", server.takeRequest(5, TimeUnit.SECONDS)!!.getHeader("Authorization"))
        assertEquals("/api/auth/guest", server.takeRequest(5, TimeUnit.SECONDS)!!.path)
        assertEquals("Bearer fresh", server.takeRequest(5, TimeUnit.SECONDS)!!.getHeader("Authorization"))
    }

    @Test fun `disconnect fails pending upload and reconnects without replaying it`() = runBlocking {
        socket { ws, _ -> ws.close(1001, "restart") }
        socket()
        connect()
        val out = withTimeout(5_000) { channel.send(byteArrayOf(1), "single") }
        assertTrue(out is FrameOutcome.Fail)
        awaitStatus("Connected")
        assertTrue(images.size == 1) // reconnect does not send the uncertain upload again
        assertTrue(withTimeout(5_000) { channel.send(byteArrayOf(2), "single") } is FrameOutcome.Ok)
        assertEquals(2, images.size)
    }

    @Test fun `pushed warnings are delivered without a pending request`() = runBlocking {
        socket { ws, id ->
            ws.send("""{"type":"reply","id":null,"reply":{"intent":"warning","screen":{"type":"flexbox","children":[{"type":"text","value":"Careful"},{"type":"text","value":"Stool ahead"}]}}}""")
            result(ws, id)
        }
        connect()
        withTimeout(5_000) { channel.send(byteArrayOf(1), "single") }
        val message = messages.poll(5, TimeUnit.SECONDS)!!
        assertEquals(MessageType.TRIGGER, message.type)
        assertEquals("Stool ahead", message.text)
    }

    @Test fun `disarming fails pending requests promptly`() = runBlocking {
        socket { _, _ -> channel.close() }
        connect()
        val out = withTimeout(5_000) { channel.send(byteArrayOf(1), "single") }
        assertTrue(out is FrameOutcome.Fail)
    }
}
