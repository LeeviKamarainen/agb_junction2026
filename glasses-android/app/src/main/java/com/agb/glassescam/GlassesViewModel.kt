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
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
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

    private enum class Mode { NONE, STREAM, QUICK }
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
                    DeviceSessionState.STARTED ->
                        if (mode == Mode.QUICK) startQuick(s) else startCamera(s)
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
                        triggerQuickCapture()
                    }
                }
                CapturePressType.DOUBLE_PRESS -> pendingShortPress?.cancel()
                else -> Unit
            }
            is InputEvent.Select -> triggerQuickCapture() // Neural Band tap/select
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
                streamJobs += viewModelScope.launch {
                    d.state.first { it == DisplayState.STARTED }
                    showOnGlasses?.invoke("Ready - press to capture")
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
            PhotoStore.save(ctx, bitmap)
            _ui.update { it.copy(photos = PhotoStore.list(ctx)) }

            // After the transfer finished, so display updates are no longer held back.
            val hasBackend = BuildConfig.BACKEND_URL.isNotBlank()
            if (hasBackend) {
                setStage(CaptureStage.SENDING, 0f, "Uploading to backend")
                showOnGlasses?.invoke("Saved. Sending...")
            }
            val sendResult = PhotoSink.send(bitmap)
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
        if (mode == Mode.QUICK) return triggerQuickCapture()
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
        _ui.update { it.copy(quickArmed = false) }
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
