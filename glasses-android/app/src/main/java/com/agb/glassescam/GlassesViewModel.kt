package com.agb.glassescam

import android.app.Activity
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.SystemClock
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
import com.meta.wearable.dat.mockdevice.api.MockDeviceKitInterface
import com.meta.wearable.dat.mockdevice.api.camera.CameraFacing
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import com.meta.wearable.dat.display.views.Alignment
import com.meta.wearable.dat.display.views.CornerRadius
import com.meta.wearable.dat.display.views.ImageSize
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlin.coroutines.resume
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
// Quick-shot (single photo, no stream). Photo types live in mwdat-camera; this package name is
// inferred from the docs. If it doesn't resolve, use Alt+Enter on PhotoResolution/PhotoQuality/PhotoState.
import com.meta.wearable.dat.camera.photo.types.PhotoQuality
import com.meta.wearable.dat.camera.photo.types.PhotoResolution
import com.meta.wearable.dat.camera.photo.types.PhotoState
import com.meta.wearable.dat.display.addDisplay
import com.meta.wearable.dat.display.types.DisplayConfiguration
import com.meta.wearable.dat.display.types.DisplayState
import com.meta.wearable.dat.display.views.Direction
import com.meta.wearable.dat.display.views.TextStyle
import com.meta.wearable.dat.inputs.addInputs
import com.meta.wearable.dat.inputs.types.CapturePressType
import com.meta.wearable.dat.inputs.types.InputEvent
import com.meta.wearable.dat.inputs.types.InputSource
import com.meta.wearable.dat.inputs.types.InputsConfiguration

/** Where a quick-shot photo is on its way from the glasses shutter to your gallery/backend. */
enum class CaptureStage { IDLE, CAPTURING, TRANSFERRING, SAVING, SENDING, DONE, FAILED }

data class UiState(
    val captureStage: CaptureStage = CaptureStage.IDLE,
    val captureProgress: Float = 0f, // 0..1, meaningful while TRANSFERRING
    val captureDetail: String = "",
    val initialized: Boolean = false,
    val registration: String = "-",
    val deviceCount: Int = 0,
    val sessionState: String = "-",
    val streamState: String = "-",
    val mockEnabled: Boolean = false,
    val photoProfile: String = "Balanced",
    val useDisplay: Boolean = false, // show messages on Ray-Ban Display glasses (can interfere with photo transfer)
    val snapArmed: Boolean = false, // snapshot mode: stream is open, trigger saves the next frame
    val liveArmed: Boolean = false, // live segment mode: segments a frame every liveIntervalMs
    val liveIntervalMs: Long = 1000L,
    val liveCrosshair: Boolean = true, // draw the target crosshair (glasses + phone)
    val liveFrameOnPhone: Boolean = true,    // phone view: full camera frame with the object highlighted
    val liveFrameOnGlasses: Boolean = false, // glasses view: same (adds light everywhere on a see-through display)
    val segmentGrid: Boolean = false,  // segment with a 3x3 grid of points (/segment-grid) instead of one pixel
    val liveStatus: String = "",
    val liveImage: Bitmap? = null,  // what was last drawn on the glasses (shown on the phone too)
    val quickArmed: Boolean = false, // quick-shot mode: no stream, glasses button/gesture takes a photo
    val photos: List<java.io.File> = emptyList(), // gallery, newest first
    val message: String = "",
)

class GlassesViewModel : ViewModel() {

    private val _ui = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = _ui

    private var session: DeviceSession? = null
    private var camera: Camera? = null
    private var stream: Stream? = null
    private var streamJobs = mutableListOf<Job>()

    private enum class Mode { NONE, STREAM, QUICK, SNAP, LIVE }

    // Live segment: the loop, and a way to push a bitmap to the glasses display (set when a display attaches).
    private var liveJob: Job? = null
    private var showBitmapOnGlasses: (suspend (Bitmap) -> Unit)? = null

    // The last live frame and what was found in it, so "move crosshair to object" and the crosshair
    // switch can redraw immediately instead of waiting for the next cycle.
    private var lastLiveJpeg: ByteArray? = null
    private var lastLiveResult: SegmentResult? = null
    private var lastLiveW = 0
    private var lastLiveH = 0
    private var liveFlash: String? = null // one-off text shown on the next drawing, e.g. "Crosshair moved"
    private val liveRenderMutex = Mutex() // never push two images to the display at once

    // Snapshot-from-stream mode: the video stream stays open and a trigger saves the next decoded frame.
    private var snapDecoder: SnapshotDecoder? = null
    private val snapLock = Any()
    private var mode = Mode.NONE
    // Quick-shot bookkeeping. Photos arrive asynchronously (and older queued ones can arrive first),
    // so we count requests vs. received instead of blocking while a transfer is in flight.
    private enum class Profile(val label: String, val res: PhotoResolution, val quality: PhotoQuality) {
        FAST("Fast", PhotoResolution.SMALL, PhotoQuality.MEDIUM),
        BALANCED("Balanced", PhotoResolution.LARGE, PhotoQuality.MEDIUM),
        FULL("Full", PhotoResolution.FULL, PhotoQuality.HIGH),
    }
    private var feedback: CaptureFeedback? = null
    private var doneResetJob: Job? = null
    private var captureWatchdog: Job? = null

    private fun setStage(stage: CaptureStage, progress: Float = 0f, detail: String = "") {
        _ui.update { it.copy(captureStage = stage, captureProgress = progress, captureDetail = detail) }
    }

    private var profile = Profile.BALANCED
    private var lastCaptureRequestMs = 0L

    // One photo at a time: true from the capture request until that photo arrives (or fails/times out).
    private var inFlight = false
    private var inFlightSinceMs = 0L
    // Older photos that the glasses are still sending from earlier sessions arrive on the same stream.
    private var lastOlderPhotoActivityMs = 0L
    private var olderPhotoCount = 0
    private var requestedCount = 0
    private var receivedCount = 0
    private var pendingShortPress: Job? = null
    private var showOnGlasses: (suspend (String) -> Unit)? = null // set only on display glasses

    private var decoder: HevcDecoder? = null
    private val decoderLock = Any()

    @Volatile
    private var surface: Surface? = null

    // ---------------------------------------------------------------- setup

    /** Call once the Bluetooth runtime permission is granted. Safe to call again. */
    private var appContext: android.content.Context? = null

    fun initialize(context: android.content.Context) {
        if (_ui.value.initialized) return
        appContext = context.applicationContext
        feedback = CaptureFeedback(context.applicationContext)
        SegmentSettings.load(context.applicationContext)
        _ui.update { it.copy(segmentGrid = SegmentSettings.useGrid) }
        _ui.update { it.copy(photos = PhotoStore.list(context.applicationContext)) }
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

    /** Opens the Meta AI app screen that updates this app's DAT integration on the glasses. */
    fun openFirmwareUpdate(activity: Activity) {
        Wearables.openDATGlassesAppUpdate(activity)
    }

    fun unregister(activity: Activity) {
        Wearables.startUnregistration(activity)
    }

    fun setSurface(s: Surface?) {
        surface = s
        if (s == null) releaseDecoder()
    }

    // ------------------------------------------------------------ mock glasses

    private var mockKit: MockDeviceKitInterface? = null

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
    fun startStreaming(requestPermission: suspend (Permission) -> PermissionStatus) =
        openSession(Mode.STREAM, requestPermission)

    /** Quick-shot: no video stream; a glasses button press or Neural Band gesture takes one photo. */
    fun armQuickShot(requestPermission: suspend (Permission) -> PermissionStatus) =
        openSession(Mode.QUICK, requestPermission)

    private fun openSession(
        newMode: Mode,
        requestPermission: suspend (Permission) -> PermissionStatus,
    ) {
        if (session != null) return say("Stop the current mode first")
        mode = newMode
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
        if (inFlight) {
            // The photo stays on the glasses and will be re-sent (and delay things) next time we arm.
            say("Disarmed while a photo was still being received. It will be sent the next time you arm.")
        }
        camera?.stop()
        session?.stop()
    }

    private fun observeSession(s: DeviceSession) {
        viewModelScope.launch {
            s.errors.collect { err ->
                val text = err.toString()
                if (text.contains("UPDATE_REQUIRED")) {
                    say("The DAT app on the glasses needs an update. Tap 'Update glasses app' (or Meta AI app > App connections), then retry.")
                } else {
                    say("Session error: $text")
                }
            }
        }
        viewModelScope.launch {
            s.state.collect { state ->
                Log.i("GlassesVM", "session state: $state")
                _ui.update { it.copy(sessionState = state.toString()) }
                when (state) {
                    DeviceSessionState.STARTED -> when (mode) {
                        Mode.QUICK -> startQuick(s)
                        Mode.SNAP -> startSnap(s)
                        Mode.LIVE -> startSnapStream(s, live = true)
                        else -> startCamera(s)
                    }
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
                    // A stream that hasn't started yet reports STOPPED/CLOSED; only treat those as
                    // "ended" after it has actually been active, or we kill it before start().
                    var wasActive = false
                    st.state.collect { state ->
                        Log.i("GlassesVM", "stream state: $state")
                        _ui.update { it.copy(streamState = state.toString()) }
                        when (state) {
                            StreamState.STARTING,
                            StreamState.STARTED,
                            StreamState.STREAMING,
                            StreamState.PAUSED -> wasActive = true
                            StreamState.STOPPED,
                            StreamState.CLOSED -> if (wasActive) stopStreaming()
                            else -> Unit
                        }
                    }
                }
                streamJobs += viewModelScope.launch(Dispatchers.Default) {
                    var frames = 0
                    st.videoStream.collect {
                        if (frames++ == 0) Log.i("GlassesVM", "first frame: ${it.width}x${it.height} compressed=${it.isCompressed}")
                        handleFrame(it)
                    }
                }
                st.start().onFailure { error, _ -> say("Stream start failed: ${error.description}") }
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

    // ------------------------------------------------------- quick shot (no stream)

    private fun startQuick(s: DeviceSession) {
        if (camera != null) return
        // Same addCamera() as streaming, but we never call stream.start(); we only use cam.photo.
        s.addCamera(StreamConfiguration(videoQuality = VideoQuality.MEDIUM, frameRate = 24)).fold(
            onSuccess = { cam ->
                camera = cam
                val photo = cam.photo // activates standalone photo capture on first access
                // Subscribe before capturing: these flows don't replay.
                streamJobs += viewModelScope.launch {
                    photo.photoStream.collect { data -> onQuickPhoto(data.imageData, data.timestamp) }
                }
                streamJobs += viewModelScope.launch {
                    photo.errors.collect {
                        inFlight = false
                        setStage(CaptureStage.FAILED, 0f, "$it")
                        feedback?.failed()
                        say(
                            if ("$it".contains("CAPTURE_FAILURE")) {
                                "The glasses took the photo but never sent it (30 s timeout). " +
                                    "Try restarting the glasses and turn off 'Glasses screen messages'."
                            } else {
                                "Photo error: $it"
                            },
                        )
                        showOnGlasses?.invoke("Capture failed")
                    }
                }
                streamJobs += viewModelScope.launch {
                    // Progress of the glasses -> phone transfer (the slow part).
                    photo.transferProgressStream.collect { p ->
                        val size = "${p.bytesReceived / 1024} / ${p.totalBytes / 1024} KB"
                        if (inFlight) {
                            setStage(CaptureStage.TRANSFERRING, p.fraction.toFloat(), size)
                        } else {
                            // Nothing requested: this is an older photo the glasses are still sending.
                            lastOlderPhotoActivityMs = SystemClock.elapsedRealtime()
                            setStage(
                                CaptureStage.TRANSFERRING,
                                p.fraction.toFloat(),
                                "Catching up on an older photo from the glasses ($size)",
                            )
                        }
                    }
                }
                streamJobs += viewModelScope.launch {
                    photo.state.collect {
                        Log.i("GlassesVM", "photo state: $it")
                        _ui.update { ui -> ui.copy(streamState = "photo $it") }
                    }
                }
                startGlassesInputs(s)
                // Meta's known issue: capture + display together can delay or block the photo transfer
                // on Ray-Ban Display. So the display is only attached when the user turns it on.
                if (_ui.value.useDisplay) startGlassesDisplay(s)
                _ui.update { it.copy(quickArmed = true) }
                say("Quick shot armed. Press the glasses capture button or use a Neural Band gesture.")
            },
            onFailure = { error, _ -> say("Camera failed: ${error.description}") },
        )
    }

    private fun startGlassesInputs(s: DeviceSession) {
        // CAPTURE_BUTTON: the physical button on Ray-Ban Meta etc. NEURAL_BAND: band gestures (Display glasses).
        s.addInputs(InputsConfiguration(sources = setOf(InputSource.CAPTURE_BUTTON, InputSource.NEURAL_BAND)))
            .onSuccess { inputs ->
                streamJobs += viewModelScope.launch {
                    inputs.events.collect { handleGlassesInput(it) }
                }
            }
            .onFailure { error, _ -> say("Glasses inputs unavailable: ${error.description}") }
    }

    private fun handleGlassesInput(event: InputEvent) {
        Log.i("GlassesVM", "glasses input: $event")
        when (event) {
            is InputEvent.Capture -> when (event.pressType) {
                // A double press arrives as SHORT_PRESS then DOUBLE_PRESS, so wait briefly and
                // drop the pending short press if a double press follows.
                CapturePressType.SHORT_PRESS -> {
                    pendingShortPress?.cancel()
                    pendingShortPress = viewModelScope.launch {
                        delay(400)
                        triggerCapture()
                    }
                }
                CapturePressType.DOUBLE_PRESS -> pendingShortPress?.cancel()
                else -> Unit
            }
            is InputEvent.Select -> triggerCapture() // Neural Band tap/select
            else -> Unit
        }
    }

    /** Best effort: only Meta Ray-Ban Display glasses have a screen; on others addDisplay just fails. */
    private fun startGlassesDisplay(s: DeviceSession) {
        s.addDisplay(DisplayConfiguration()).fold(
            onSuccess = { d ->
                showOnGlasses = { message ->
                    if (d.state.value == DisplayState.STARTED) {
                        d.sendContent {
                            flexBox(direction = Direction.COLUMN, gap = 12) {
                                text(message, style = TextStyle.HEADING)
                            }
                        }
                    }
                }
                // Full-screen image (live segment mode). image(bitmap = ...) replaces the whole display, so
                // it must be one flexBox with a single image; FILL stretches it over the 600x600 screen.
                showBitmapOnGlasses = { bitmap ->
                    if (d.state.value == DisplayState.STARTED) {
                        d.sendContent {
                            flexBox(
                                direction = Direction.COLUMN,
                                alignment = Alignment.CENTER,
                                crossAlignment = Alignment.CENTER,
                            ) {
                                image(bitmap = bitmap, sizePreset = ImageSize.FILL, cornerRadius = CornerRadius.NONE)
                            }
                        }
                    }
                }
                streamJobs += viewModelScope.launch {
                    d.state.first { it == DisplayState.STARTED }
                    showOnGlasses?.invoke(if (mode == Mode.LIVE) "Live segment starting..." else "Ready - press to capture")
                }
            },
            onFailure = { error, _ -> Log.i("GlassesVM", "no glasses display: ${error.description}") },
        )
    }

    /** Takes effect the next time quick shot is armed. */
    fun setUseDisplay(enabled: Boolean) {
        _ui.update { it.copy(useDisplay = enabled) }
    }

    /** Cycles the quick-shot photo size: Fast (480p) -> Balanced (1080p) -> Full (4032x3024). */
    fun cycleProfile() {
        profile = Profile.entries[(profile.ordinal + 1) % Profile.entries.size]
        _ui.update { it.copy(photoProfile = profile.label) }
    }

    // ------------------------------------------------ snapshot from the video stream

    /**
     * Snapshot mode: keeps the (compressed, tens-of-KB-per-frame) video stream open and, on a trigger,
     * saves the next decoded frame as a JPEG. Near-instant and nothing queues up on the glasses, but the
     * picture is limited to stream resolution (up to 720x1280) instead of a full still photo.
     */
    fun armSnapshot(requestPermission: suspend (Permission) -> PermissionStatus) =
        openSession(Mode.SNAP, requestPermission)

    private fun startSnap(s: DeviceSession) = startSnapStream(s, live = false)

    /** Shared by snapshot mode and live segment mode: open the stream and feed [SnapshotDecoder]. */
    private fun startSnapStream(s: DeviceSession, live: Boolean) {
        if (camera != null) return
        val config = StreamConfiguration(
            // Snapshots want the best frame (720x1280). Live mode sends a frame to the API every second and
            // shares Bluetooth with display updates, so it uses the smaller 504x896.
            videoQuality = if (live) VideoQuality.MEDIUM else VideoQuality.HIGH,
            frameRate = 7,                    // low frame rate keeps Bluetooth bandwidth for resolution
            compressVideo = true,
        )
        s.addCamera(config).fold(
            onSuccess = { cam ->
                camera = cam
                val st = cam.stream
                stream = st
                streamJobs += viewModelScope.launch {
                    var wasActive = false
                    st.state.collect { state ->
                        Log.i("GlassesVM", "snapshot stream state: $state")
                        _ui.update { it.copy(streamState = "snapshot $state") }
                        when (state) {
                            StreamState.STARTING,
                            StreamState.STARTED,
                            StreamState.STREAMING,
                            StreamState.PAUSED -> wasActive = true
                            StreamState.STOPPED,
                            StreamState.CLOSED -> if (wasActive) stopStreaming()
                            else -> Unit
                        }
                    }
                }
                streamJobs += viewModelScope.launch(Dispatchers.Default) {
                    var frames = 0
                    st.videoStream.collect {
                        if (frames++ == 0) Log.i("GlassesVM", "snapshot: first frame ${it.width}x${it.height}")
                        handleSnapFrame(it)
                    }
                }
                if (live) {
                    startGlassesInputs(s)  // capture button / Neural Band = "move crosshair to the object"
                    startGlassesDisplay(s) // live mode renders on the glasses screen, so the display is required
                    st.start().onFailure { error, _ -> say("Stream start failed: ${error.description}") }
                    _ui.update { it.copy(liveArmed = true) }
                    say("Live segment armed. Put the object on the cyan crosshair.")
                    startLiveLoop()
                } else {
                    startGlassesInputs(s)
                    if (_ui.value.useDisplay) startGlassesDisplay(s)
                    st.start().onFailure { error, _ -> say("Stream start failed: ${error.description}") }
                    _ui.update { it.copy(snapArmed = true) }
                    say("Snapshot mode armed. Press the glasses capture button or use a Neural Band gesture.")
                }
            },
            onFailure = { error, _ -> say("Camera failed: ${error.description}") },
        )
    }

    // -------------------------------------------------------------- live segment

    /**
     * Live segment: every [UiState.liveIntervalMs] a frame from the stream is segmented at the calibrated eye
     * point and the result (cut-out, boxes, target crosshair) is drawn on the glasses, so the wearer can move
     * the object onto the crosshair. Runs one request at a time: the next one starts [liveIntervalMs] after
     * the previous started, or right after it finishes if it took longer.
     */
    fun armLive(requestPermission: suspend (Permission) -> PermissionStatus) =
        openSession(Mode.LIVE, requestPermission)

    /** Change how often a frame is sent (takes effect on the next cycle). Clamped to 250 ms .. 10 s. */
    fun changeLiveInterval(deltaMs: Long) {
        _ui.update { it.copy(liveIntervalMs = (it.liveIntervalMs + deltaMs).coerceIn(250L, 10_000L)) }
    }

    private fun startLiveLoop() {
        liveJob?.cancel()
        liveJob = viewModelScope.launch {
            while (isActive) {
                val started = SystemClock.elapsedRealtime()
                runCatching { liveCycle() }.onFailure {
                    if (it is CancellationException) throw it
                    Log.w("GlassesVM", "live cycle failed", it)
                    _ui.update { ui -> ui.copy(liveStatus = "Error: ${it.message}") }
                }
                val wait = _ui.value.liveIntervalMs - (SystemClock.elapsedRealtime() - started)
                delay(wait.coerceAtLeast(100L))
            }
        }
    }

    private suspend fun liveCycle() {
        val decoder = synchronized(snapLock) { snapDecoder }
            ?: return _ui.update { it.copy(liveStatus = "Waiting for video...") }

        // 1. Grab the next decoded frame as a JPEG.
        val jpeg = withTimeoutOrNull(3_000) {
            suspendCancellableCoroutine<ByteArray> { cont ->
                decoder.requestSnapshot { cont.resume(it) }
                cont.invokeOnCancellation { decoder.cancelSnapshot() }
            }
        } ?: run {
            decoder.cancelSnapshot()
            return _ui.update { it.copy(liveStatus = "No video frame") }
        }
        val (w, h) = withContext(Dispatchers.Default) {
            val o = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
            android.graphics.BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, o)
            o.outWidth to o.outHeight
        }

        // 2. Segment at the eye point (where the object should be placed).
        val (fx, fy) = appContext?.let { EyeCalibration.get(it) } ?: (0.5f to 0.5f)
        val t0 = SystemClock.elapsedRealtime()
        val outcome = if (SegmentApi.configured) SegmentApi.segment(jpeg, fx * w, fy * h) else null
        val took = SystemClock.elapsedRealtime() - t0
        val result = (outcome as? SegmentOutcome.Ok)?.result
        val baseLabel = when (outcome) {
            is SegmentOutcome.Ok ->
                "object ${String.format(java.util.Locale.US, "%.2f", outcome.result.score)}"
            is SegmentOutcome.Fail -> outcome.message
            null -> "set segment.token"
        }

        // Remember this frame for "move crosshair to object".
        lastLiveJpeg = jpeg
        lastLiveResult = result
        lastLiveW = w
        lastLiveH = h

        // 3. Draw it and show it on the glasses (and on the phone).
        val flash = liveFlash
        liveFlash = null
        renderAndShow(jpeg, w, h, result, flash ?: baseLabel, "(${took} ms${if (SegmentSettings.useGrid) ", 3x3 grid" else ""})")
    }

    /**
     * Draws the live picture twice: the glasses version (cut-out on black) and the phone version (the same on top
     * of the dimmed camera frame), shows the phone one in the app and pushes the glasses one to the display.
     */
    private suspend fun renderAndShow(
        jpeg: ByteArray,
        w: Int,
        h: Int,
        result: SegmentResult?,
        label: String,
        timing: String = "",
    ) {
        val (fx, fy) = appContext?.let { EyeCalibration.get(it) } ?: (0.5f to 0.5f)
        val crosshair = _ui.value.liveCrosshair
        val frameOnPhone = _ui.value.liveFrameOnPhone
        val frameOnGlasses = _ui.value.liveFrameOnGlasses
        val (glasses, phone) = withContext(Dispatchers.Default) {
            val g = LiveRenderer.compose(jpeg, w, h, result, fx, fy, label, showFrame = frameOnGlasses, showCrosshair = crosshair)
            // Same setting for both: draw once and use the picture for both.
            val p = if (frameOnPhone == frameOnGlasses) g else
                LiveRenderer.compose(jpeg, w, h, result, fx, fy, label, showFrame = frameOnPhone, showCrosshair = crosshair)
            g to p
        }
        liveRenderMutex.withLock {
            _ui.update { it.copy(liveImage = phone, liveStatus = "$label $timing".trim()) }
            showBitmapOnGlasses?.invoke(glasses)
        }
    }

    /**
     * The wearer has lined the camera up on the object and presses the button (phone, glasses capture button or
     * Neural Band): the crosshair (the calibrated eye point) moves to where the object currently is in the frame.
     * Later cycles then segment and draw the crosshair there.
     */
    fun setCrosshairToObject() {
        val r = lastLiveResult
        val w = lastLiveW
        val h = lastLiveH
        val ctx = appContext
        if (mode != Mode.LIVE || r == null || r.box.size != 4 || w == 0 || h == 0 || ctx == null) {
            feedback?.failed()
            say("No object found yet. Aim the glasses at the object, wait for the green box, then press again.")
            return
        }
        val fx = ((r.box[0] + r.box[2]) / 2f) / w
        val fy = ((r.box[1] + r.box[3]) / 2f) / h
        EyeCalibration.set(ctx, fx, fy)
        feedback?.done()
        say("Crosshair moved to the object (${(fx * 100).toInt()}%, ${(fy * 100).toInt()}% of the frame).")
        val jpeg = lastLiveJpeg ?: return
        viewModelScope.launch { renderAndShow(jpeg, w, h, r, "crosshair moved") }
    }

    /**
     * The user tapped the live picture on the phone to place the crosshair there.
     * [u] and [v] are the tap position as fractions (0..1) of the 600x600 live picture, which shows the camera frame
     * fitted (letterboxed) inside it, so the tap is mapped back to a spot in the camera frame first.
     */
    fun setCrosshairFromImageTap(u: Float, v: Float) {
        val w = lastLiveW
        val h = lastLiveH
        val ctx = appContext
        if (mode != Mode.LIVE || w == 0 || h == 0 || ctx == null) return
        val size = LiveRenderer.SIZE.toFloat()
        val scale = minOf(size / w, size / h)
        val ox = (size - w * scale) / 2f
        val oy = (size - h * scale) / 2f
        val px = (u * size - ox) / scale
        val py = (v * size - oy) / scale
        if (px < 0f || py < 0f || px > w || py > h) {
            say("Tap inside the camera picture to place the crosshair.")
            return
        }
        EyeCalibration.set(ctx, px / w, py / h)
        feedback?.done()
        say("Crosshair placed at (${(px / w * 100).toInt()}%, ${(py / h * 100).toInt()}% of the frame).")
        val jpeg = lastLiveJpeg ?: return
        viewModelScope.launch { renderAndShow(jpeg, w, h, lastLiveResult, "crosshair placed") }
    }

    /** Segment with a 3x3 grid of points (/segment-grid) instead of a single pixel. Applies to every request. */
    fun setSegmentGrid(enabled: Boolean) {
        appContext?.let { SegmentSettings.setUseGrid(it, enabled) } ?: run { SegmentSettings.useGrid = enabled }
        _ui.update { it.copy(segmentGrid = enabled) }
    }

    /** Full camera frame (object highlighted) in the phone's live view, instead of only the cut-out. */
    fun setLiveFrameOnPhone(enabled: Boolean) {
        _ui.update { it.copy(liveFrameOnPhone = enabled) }
        redrawLive(if (enabled) "full frame on phone" else "cut-out only on phone")
    }

    /** Full camera frame (object highlighted) on the glasses display too. Off by default. */
    fun setLiveFrameOnGlasses(enabled: Boolean) {
        _ui.update { it.copy(liveFrameOnGlasses = enabled) }
        redrawLive(if (enabled) "full frame on glasses" else "cut-out only on glasses")
    }

    /** Redraws the last live frame with the current display settings, without waiting for the next cycle. */
    private fun redrawLive(label: String) {
        val jpeg = lastLiveJpeg ?: return
        if (mode == Mode.LIVE) viewModelScope.launch {
            renderAndShow(jpeg, lastLiveW, lastLiveH, lastLiveResult, label)
        }
    }

    /** Show or hide the crosshair on the glasses and the phone preview. */
    fun setLiveCrosshair(visible: Boolean) {
        _ui.update { it.copy(liveCrosshair = visible) }
        val jpeg = lastLiveJpeg ?: return
        if (mode == Mode.LIVE) viewModelScope.launch {
            renderAndShow(jpeg, lastLiveW, lastLiveH, lastLiveResult, if (visible) "crosshair on" else "crosshair off")
        }
    }

    private fun handleSnapFrame(frame: VideoFrame) {
        if (!frame.isCompressed) return
        val buf = frame.buffer
        val pos = buf.position()
        val bytes = ByteArray(buf.remaining())
        buf.get(bytes)
        buf.position(pos)

        synchronized(snapLock) {
            val d = snapDecoder ?: SnapshotDecoder(frame.width, frame.height).also { snapDecoder = it }
            d.decode(bytes, frame.presentationTimeUs, frame.isCodecConfig)
        }
    }

    private fun releaseSnapDecoder() {
        synchronized(snapLock) {
            snapDecoder?.release()
            snapDecoder = null
        }
    }

    /**
     * Sends the photo to the segmentation API with the point at the image centre (the object the wearer
     * was looking at) and stores the answer next to the photo. Returns a short summary, or "" if the API
     * isn't configured. Tap an object in the gallery to segment a different point.
     */
    private suspend fun runSegmentation(
        photo: java.io.File,
        width: Int,
        height: Int,
        jpeg: ByteArray? = null,
    ): String {
        if (!SegmentApi.configured) return ""
        // The calibrated "straight ahead" spot (the middle of the glasses), not the middle of the photo.
        val (fx, fy) = appContext?.let { EyeCalibration.get(it) } ?: (0.5f to 0.5f)
        val x = fx * width
        val y = fy * height
        setStage(CaptureStage.SENDING, 0f, "Segmenting at the eye point (${x.toInt()}, ${y.toInt()})")
        val bytes = jpeg ?: withContext(Dispatchers.IO) { photo.readBytes() }
        return when (val out = SegmentApi.segment(bytes, x, y)) {
            is SegmentOutcome.Ok -> {
                PhotoStore.saveSegmentJson(photo, out.result.rawJson)
                "Segment${if (SegmentSettings.useGrid) " (3x3 grid)" else ""}: score " +
                    "${String.format(java.util.Locale.US, "%.2f", out.result.score)} box ${out.result.box}"
            }
            is SegmentOutcome.Fail -> "Segment failed: ${out.message}"
        }
    }

    private fun triggerCapture() {
        when (mode) {
            Mode.SNAP -> triggerSnapshot()
            Mode.LIVE -> setCrosshairToObject() // glasses capture button / Neural Band select
            else -> triggerQuickCapture()
        }
    }

    private fun triggerSnapshot() {
        val now = SystemClock.elapsedRealtime()
        if (now - lastCaptureRequestMs < 600) return // double-fire guard
        val decoder = snapDecoder ?: return say("No video frames yet. Wait a second after arming and try again.")
        if (decoder.hasPending) return say("Busy: still grabbing the previous frame.")
        lastCaptureRequestMs = now

        doneResetJob?.cancel()
        setStage(CaptureStage.CAPTURING, 0f, "Grabbing the next video frame")
        feedback?.started()
        decoder.requestSnapshot { jpeg -> onSnapshotJpeg(jpeg) }

        // Frames normally arrive several times a second; if none does, tell the user instead of hanging.
        viewModelScope.launch {
            delay(4_000)
            val d = snapDecoder
            if (d != null && d.hasPending) {
                d.cancelSnapshot()
                setStage(CaptureStage.FAILED, 0f, "No video frame arrived within 4 s")
                feedback?.failed()
                say("No video frame arrived. Is the glasses stream running?")
            }
        }
    }

    private fun onSnapshotJpeg(jpeg: ByteArray) {
        val ctx = appContext ?: return
        viewModelScope.launch {
            setStage(CaptureStage.SAVING, 1f, "${jpeg.size / 1024} KB frame")
            val size = withContext(Dispatchers.Default) {
                val o = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
                android.graphics.BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, o)
                o.outWidth to o.outHeight
            }
            val savedFile = PhotoStore.saveJpeg(ctx, jpeg)
            _ui.update { it.copy(photos = PhotoStore.list(ctx)) }

            val hasBackend = BuildConfig.BACKEND_URL.isNotBlank()
            if (hasBackend) {
                setStage(CaptureStage.SENDING, 0f, "Uploading to backend")
                showOnGlasses?.invoke("Saved. Sending...")
            }
            val segment = runSegmentation(savedFile, size.first, size.second, jpeg)
            val sendResult = PhotoSink.sendJpeg(jpeg, size.first, size.second) +
                (if (segment.isNotEmpty()) " | $segment" else "")
            say("Snapshot ${size.first}x${size.second} (${jpeg.size / 1024} KB). $sendResult")

            feedback?.done()
            setStage(CaptureStage.DONE, 1f, sendResult)
            doneResetJob?.cancel()
            doneResetJob = viewModelScope.launch {
                delay(5_000)
                if (_ui.value.captureStage == CaptureStage.DONE) setStage(CaptureStage.IDLE)
            }
            showOnGlasses?.invoke(if (hasBackend) "Photo saved and sent (${_ui.value.photos.size})" else "Photo saved (${_ui.value.photos.size})")
        }
    }

    private fun triggerQuickCapture() {
        val cam = camera ?: return say("Quick shot is not armed")
        val now = SystemClock.elapsedRealtime()
        if (now - lastCaptureRequestMs < 800) return // double-fire guard

        // One photo at a time: no queueing of requests.
        if (inFlight && now - inFlightSinceMs < 120_000) {
            return say("Busy: still receiving the previous photo. Wait for it to finish, then try again.")
        }
        // Older photos from earlier sessions are still being sent; our new photo would queue behind
        // them and we couldn't tell them apart, so wait until that has finished.
        if (now - lastOlderPhotoActivityMs < 8_000) {
            return say("Still catching up on older photos from the glasses. Wait until that finishes, then capture.")
        }
        lastCaptureRequestMs = now
        inFlight = true
        inFlightSinceMs = now
        requestedCount++
        say("Capturing (${profile.label})... requested $requestedCount, received $receivedCount")
        doneResetJob?.cancel()
        setStage(CaptureStage.CAPTURING, 0f, "${profile.label} photo requested")
        feedback?.started()
        viewModelScope.launch {
            val photo = cam.photo
            val ready = withTimeoutOrNull(10_000) { photo.state.first { it == PhotoState.STARTED } }
            if (ready == null) {
                requestedCount--
                inFlight = false
                setStage(CaptureStage.FAILED, 0f, "Camera not ready")
                feedback?.failed()
                return@launch say("Camera not ready yet, try again")
            }
            // Show this BEFORE the capture: display updates are held back while a photo transfers.
            showOnGlasses?.invoke("Capturing...")
            // The result arrives later on photo.photoStream. The link is slow (Bluetooth) and the glasses
            // may still be sending older queued photos first, so bigger photos take noticeably longer.
            photo.capturePhoto(resolution = profile.res, quality = profile.quality)
        }
        captureWatchdog?.cancel()
        captureWatchdog = viewModelScope.launch {
            delay(90_000)
            val st = _ui.value.captureStage
            if ((st == CaptureStage.CAPTURING || st == CaptureStage.TRANSFERRING) && receivedCount < requestedCount) {
                inFlight = false
                setStage(CaptureStage.FAILED, 0f, "Still waiting after 90 s. The glasses may be sending older photos first.")
                feedback?.failed()
            }
        }
    }

    private fun onQuickPhoto(bytes: ByteArray, captureTimestamp: Long) {
        val ctx = appContext ?: return
        val solicited = inFlight // did we ask for this photo?
        inFlight = false
        viewModelScope.launch {
            if (!solicited) {
                // An older photo that was left on the glasses by an earlier session. Keep it (it is
                // someone's photo) but don't treat it as the result of a capture or send it to the backend.
                lastOlderPhotoActivityMs = SystemClock.elapsedRealtime()
                val old = withContext(Dispatchers.Default) { PhotoStore.decodeHeic(bytes) }
                if (old != null) {
                    olderPhotoCount++
                    PhotoStore.save(ctx, old)
                    _ui.update { it.copy(photos = PhotoStore.list(ctx)) }
                }
                Log.i("GlassesVM", "older photo from glasses saved: ${bytes.size} bytes (#$olderPhotoCount)")
                setStage(CaptureStage.TRANSFERRING, 1f, "Older photo #$olderPhotoCount from the glasses saved to the gallery")
                return@launch
            }
            // imageData is JPEG or HEIC; decodeHeic handles both and applies EXIF rotation.
            setStage(CaptureStage.SAVING, 1f, "${bytes.size / 1024} KB received")
            val bitmap = withContext(Dispatchers.Default) { PhotoStore.decodeHeic(bytes) }
            if (bitmap == null) {
                setStage(CaptureStage.FAILED, 0f, "Could not decode photo")
                feedback?.failed()
                return@launch say("Could not decode photo")
            }
            receivedCount++
            Log.i("GlassesVM", "photo received: ${bytes.size} bytes, arrived at $captureTimestamp ms (phone time, not shutter time)")
            val savedFile = PhotoStore.save(ctx, bitmap)
            _ui.update { it.copy(photos = PhotoStore.list(ctx)) }

            // After the transfer finished, so display updates are no longer held back.
            val hasBackend = BuildConfig.BACKEND_URL.isNotBlank()
            if (hasBackend) {
                setStage(CaptureStage.SENDING, 0f, "Uploading to backend")
                showOnGlasses?.invoke("Saved. Sending...")
            }
            val segment = runSegmentation(savedFile, bitmap.width, bitmap.height)
            val sendResult = PhotoSink.send(bitmap) + (if (segment.isNotEmpty()) " | $segment" else "")
            say("Saved ${bitmap.width}x${bitmap.height} (${bytes.size / 1024} KB). received $receivedCount of $requestedCount requested. $sendResult")

            feedback?.done()
            val stillWaiting = requestedCount - receivedCount
            if (stillWaiting > 0) {
                setStage(CaptureStage.CAPTURING, 0f, "Saved one photo, waiting for $stillWaiting more")
            } else {
                captureWatchdog?.cancel()
                setStage(CaptureStage.DONE, 1f, sendResult)
                doneResetJob?.cancel()
                doneResetJob = viewModelScope.launch {
                    delay(5_000)
                    if (_ui.value.captureStage == CaptureStage.DONE) setStage(CaptureStage.IDLE)
                }
            }
            showOnGlasses?.invoke(if (hasBackend) "Photo saved and sent (${_ui.value.photos.size})" else "Photo saved (${_ui.value.photos.size})")
        }
    }

    // ------------------------------------------------------------------ photo

    /**
     * Takes a full-resolution still from the glasses (separate from the lower-res live preview),
     * saves it to the gallery, then hands it to [PhotoSink].
     */
    fun capturePhoto() {
        if (mode == Mode.LIVE) return setCrosshairToObject() // in live mode the Capture button moves the crosshair
        if (mode == Mode.QUICK || mode == Mode.SNAP) return triggerCapture()
        val st = stream ?: return say("Start streaming first")
        val ctx = appContext ?: return say("Not initialized yet")
        viewModelScope.launch {
            // capturePhoto() is a suspend function
            st.capturePhoto()
                .onSuccess { data ->
                    launch {
                        val bitmap = withContext(Dispatchers.Default) {
                            when (data) {
                                is PhotoData.Bitmap -> data.bitmap
                                is PhotoData.HEIC -> {
                                    val b = data.data
                                    val bytes = ByteArray(b.remaining()).also { b.duplicate().get(it) }
                                    PhotoStore.decodeHeic(bytes)
                                }
                            }
                        }
                        if (bitmap == null) return@launch say("Could not decode photo")
                        PhotoStore.save(ctx, bitmap)
                        _ui.update { it.copy(photos = PhotoStore.list(ctx)) }
                        say("Saved ${bitmap.width}x${bitmap.height} photo. ${PhotoSink.send(bitmap)}")
                    }
                }
                .onFailure { error, _ -> say("Capture failed: ${error.description}") }
        }
    }

    /** Reloads the gallery list (e.g. after the gallery created an eye-centred crop). */
    fun refreshPhotos() {
        val ctx = appContext ?: return
        _ui.update { it.copy(photos = PhotoStore.list(ctx)) }
    }

    fun deletePhoto(file: java.io.File) {
        val ctx = appContext ?: return
        viewModelScope.launch {
            PhotoStore.delete(file)
            _ui.update { it.copy(photos = PhotoStore.list(ctx)) }
        }
    }

    // --------------------------------------------------------------- teardown

    private fun cleanup() {
        streamJobs.forEach { it.cancel() }
        streamJobs.clear()
        releaseDecoder()
        pendingShortPress?.cancel()
        pendingShortPress = null
        showOnGlasses = null
        requestedCount = 0
        receivedCount = 0
        inFlight = false
        lastOlderPhotoActivityMs = 0L
        olderPhotoCount = 0
        captureWatchdog?.cancel()
        doneResetJob?.cancel()
        setStage(CaptureStage.IDLE)
        mode = Mode.NONE
        releaseSnapDecoder()
        liveJob?.cancel()
        liveJob = null
        showBitmapOnGlasses = null
        lastLiveJpeg = null
        lastLiveResult = null
        liveFlash = null
        _ui.update { it.copy(quickArmed = false, snapArmed = false, liveArmed = false, liveImage = null, liveStatus = "") }
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
        feedback?.release()
        stopStreaming()
        cleanup()
    }
}
