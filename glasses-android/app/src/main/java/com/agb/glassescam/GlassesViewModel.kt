package com.agb.glassescam

import android.app.Activity
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import android.view.Surface
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.meta.wearable.dat.camera.Camera
import com.meta.wearable.dat.camera.Stream
import com.meta.wearable.dat.camera.addCamera
import com.meta.wearable.dat.camera.types.PhotoData
import com.meta.wearable.dat.camera.types.StreamConfiguration
import com.meta.wearable.dat.camera.types.StreamState
import com.meta.wearable.dat.camera.types.VideoFrame
import com.meta.wearable.dat.camera.types.VideoQuality
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import com.meta.wearable.dat.mockdevice.MockDeviceKit
import com.meta.wearable.dat.mockdevice.api.GlassesModel
import com.meta.wearable.dat.mockdevice.api.camera.CameraFacing
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class UiState(
    val initialized: Boolean = false,
    val registration: String = "-",
    val deviceCount: Int = 0,
    val sessionState: String = "-",
    val streamState: String = "-",
    val mockEnabled: Boolean = false,
    val lastPhoto: Bitmap? = null,
    val message: String = "",
)

class GlassesViewModel : ViewModel() {

    private val _ui = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = _ui

    private var session: DeviceSession? = null
    private var camera: Camera? = null
    private var stream: Stream? = null
    private var streamJobs = mutableListOf<Job>()

    private var decoder: HevcDecoder? = null
    private val decoderLock = Any()

    @Volatile
    private var surface: Surface? = null

    // ---------------------------------------------------------------- setup

    /** Call once the Bluetooth runtime permission is granted. Safe to call again. */
    fun initialize(context: android.content.Context) {
        if (_ui.value.initialized) return
        // A second initialize() in the same process returns ALREADY_INITIALIZED; that's fine.
        Wearables.initialize(context)
        _ui.update { it.copy(initialized = true) }

        viewModelScope.launch {
            Wearables.registrationState.collect { state ->
                _ui.update { it.copy(registration = state.toString()) }
            }
        }
        viewModelScope.launch {
            Wearables.devices.collect { devices ->
                _ui.update { it.copy(deviceCount = devices.toList().size) }
            }
        }
    }

    /** Hands off to the Meta AI app to approve this app. Only one dev-mode app at a time. */
    fun register(activity: Activity) {
        Wearables.startRegistration(activity)
    }

    fun unregister(activity: Activity) {
        Wearables.startUnregistration(activity)
    }

    fun setSurface(s: Surface?) {
        surface = s
        if (s == null) releaseDecoder()
    }

    // ------------------------------------------------------------ mock glasses

    private var mockKit: MockDeviceKit? = null

    /**
     * Pairs a fake Ray-Ban Meta that uses the phone's back camera as its feed.
     * Needs android.permission.CAMERA granted first, and [initialize] already called.
     * Registration completes automatically while the kit is enabled.
     */
    fun enableMock(context: android.content.Context) {
        if (mockKit != null) return
        viewModelScope.launch {
            try {
                val kit = MockDeviceKit.getInstance(context.applicationContext)
                kit.enable()
                val device = kit.pairGlasses(GlassesModel.RAYBAN_META).getOrThrow()
                device.powerOn()
                device.don()
                device.unfold()
                device.services.camera.setCameraFeed(CameraFacing.BACK)
                mockKit = kit
                _ui.update { it.copy(mockEnabled = true) }
                say("Mock glasses ready (phone camera feed)")
            } catch (e: Exception) {
                say("Mock setup failed: ${e.message}")
            }
        }
    }

    fun disableMock() {
        stopStreaming()
        try {
            mockKit?.disable()
        } catch (e: Exception) {
            Log.w("GlassesVM", "mock disable failed", e)
        }
        mockKit = null
        _ui.update { it.copy(mockEnabled = false) }
    }

    // -------------------------------------------------------------- streaming

    /**
     * @param requestPermission shows the Meta AI app's permission sheet; supplied by the Activity.
     */
    fun startStreaming(requestPermission: suspend (Permission) -> PermissionStatus) {
        if (session != null) return
        viewModelScope.launch {
            val current = Wearables.checkPermissionStatus(Permission.CAMERA)
                .getOrDefault(PermissionStatus.Denied)
            if (current != PermissionStatus.Granted &&
                requestPermission(Permission.CAMERA) != PermissionStatus.Granted
            ) {
                say("Camera permission denied")
                return@launch
            }

            Wearables.createSession(AutoDeviceSelector())
                .onSuccess { s ->
                    session = s
                    observeSession(s)
                    s.start() // returns immediately; connects in the background
                    say("Connecting to glasses...")
                }
                .onFailure { error, _ -> say("Session failed: ${error.description}") }
        }
    }

    fun stopStreaming() {
        camera?.stop()
        session?.stop()
    }

    private fun observeSession(s: DeviceSession) {
        viewModelScope.launch {
            s.errors.collect { say("Session error: $it") }
        }
        viewModelScope.launch {
            s.state.collect { state ->
                _ui.update { it.copy(sessionState = state.toString()) }
                when (state) {
                    DeviceSessionState.STARTED -> startCamera(s)
                    DeviceSessionState.STOPPED -> cleanup()
                    else -> Unit
                }
            }
        }
    }

    private fun startCamera(s: DeviceSession) {
        if (camera != null) return
        val config = StreamConfiguration(
            videoQuality = VideoQuality.MEDIUM, // HIGH 720x1280, MEDIUM 504x896, LOW 360x640
            frameRate = 24,                     // 2, 7, 15, 24 or 30
            compressVideo = true,               // HEVC frames -> HevcDecoder
        )
        s.addCamera(config).fold(
            onSuccess = { cam ->
                camera = cam
                val st = cam.stream
                stream = st
                streamJobs += viewModelScope.launch {
                    st.state.collect { state ->
                        _ui.update { it.copy(streamState = state.toString()) }
                        if (state == StreamState.STOPPED || state == StreamState.CLOSED) {
                            stopStreaming()
                        }
                    }
                }
                streamJobs += viewModelScope.launch(Dispatchers.Default) {
                    st.videoStream.collect { handleFrame(it) }
                }
                st.start()
            },
            onFailure = { error, _ -> say("Camera failed: ${error.description}") },
        )
    }

    private fun handleFrame(frame: VideoFrame) {
        if (!frame.isCompressed) return
        val buf = frame.buffer
        val pos = buf.position()
        val bytes = ByteArray(buf.remaining())
        buf.get(bytes)
        buf.position(pos)

        synchronized(decoderLock) {
            val s = surface ?: return
            val d = decoder ?: HevcDecoder(s, frame.width, frame.height).also { decoder = it }
            d.decode(bytes, frame.presentationTimeUs, frame.isCodecConfig)
        }
    }

    // ------------------------------------------------------------------ photo

    /** Takes a still from the glasses while streaming and hands it to [PhotoSink]. */
    fun capturePhoto() {
        val st = stream ?: return say("Start streaming first")
        st.capturePhoto()
            .onSuccess { data ->
                val bitmap = when (data) {
                    is PhotoData.Bitmap -> data.bitmap
                    is PhotoData.HEIC -> {
                        val b = data.data
                        val bytes = ByteArray(b.remaining()).also { b.duplicate().get(it) }
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    }
                }
                if (bitmap == null) return@onSuccess say("Could not decode photo")
                _ui.update { it.copy(lastPhoto = bitmap) }
                viewModelScope.launch { say(PhotoSink.send(bitmap)) }
            }
            .onFailure { error, _ -> say("Capture failed: ${error.description}") }
    }

    // --------------------------------------------------------------- teardown

    private fun cleanup() {
        streamJobs.forEach { it.cancel() }
        streamJobs.clear()
        releaseDecoder()
        camera = null
        stream = null
        session = null // a stopped session can't be restarted; create a new one next time
    }

    private fun releaseDecoder() {
        synchronized(decoderLock) {
            decoder?.release()
            decoder = null
        }
    }

    private fun say(msg: String) {
        Log.i("GlassesVM", msg)
        _ui.update { it.copy(message = msg) }
    }

    override fun onCleared() {
        stopStreaming()
        cleanup()
    }
}
