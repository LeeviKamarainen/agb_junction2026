package com.agb.glassescam

import android.content.Context
import android.content.Intent
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.Executors

/**
 * Push-to-talk speech to text: Android's own speech recognizer, listening through the glasses'
 * microphone. The glasses are a Bluetooth headset to the phone, so for the recording the phone's
 * communication device is set to their Bluetooth SCO (hands-free) input; without one the phone's own
 * microphone is used, and [mic] says which.
 *
 * The recognizer stops by itself when the wearer pauses. That pause is not the end of the message: what was
 * heard so far is kept and listening starts again. A message ends when [finish] is called (a button) or by
 * itself: [SILENCE_END_MS] after the last words, or [NO_SPEECH_MS] after the start if nothing was said, when
 * [onSilence] is called (it should call [finish]). Use from the main thread only (SpeechRecognizer requires it).
 *
 * Alternatively ([start] with `fromPcm`) the recognizer is not given a microphone at all but the PCM audio of the
 * glasses' camera stream, which the caller passes to [feedPcm]: 16 kHz, mono, 16 bit, higher quality than
 * the Bluetooth hands-free route and without its set-up delay. Android 13+ only, and up to the recognizer
 * service whether it honours the audio source (it must, or nothing is heard: see [pcmBytes]).
 */
class VoiceInput(
    private val context: Context,
    private val onPartial: (String) -> Unit,
    private val onError: (String) -> Unit,
    private val onSilence: () -> Unit = {},
    /** The microphone is routed and ready: play the "speak now" beep ([viaBluetooth]: through the glasses). */
    private val onReady: (viaBluetooth: Boolean) -> Unit = {},
) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private var recognizer: SpeechRecognizer? = null
    private val heard = mutableListOf<String>() // finished pieces of this message
    private var finishing = false
    private var done: CompletableDeferred<Unit>? = null
    private var previousMode: Int? = null
    private var busyRetries = 0
    // The newest non-empty partial transcript not yet confirmed by a final result. Google's recognizer reading
    // the camera stream's audio gave partials ("hello how are you") but then an empty final result, so
    // without this everything said was thrown away.
    private var pendingPartial = ""
    private val main = Handler(Looper.getMainLooper())
    private val silenceTimer = Runnable { if (recognizer != null && !finishing) onSilence() }

    /** What is recording: the glasses' name, or "phone microphone". */
    var mic: String = ""
        private set

    val available: Boolean get() = SpeechRecognizer.isRecognitionAvailable(context)

    // Audio from the camera stream goes through this pipe to the recognizer (EXTRA_AUDIO_SOURCE).
    private var pipeRead: ParcelFileDescriptor? = null
    private var pipeWrite: ParcelFileDescriptor.AutoCloseOutputStream? = null
    private var pipeWriter: java.util.concurrent.ExecutorService? = null // a full pipe blocks: never on the caller's thread

    /** Bytes of camera-stream audio given to the recognizer since [start]: 0 means no audio came from the glasses. */
    @Volatile var pcmBytes = 0L
        private set

    /** The camera-stream audio mode can be used on this phone. */
    val pcmSupported: Boolean get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU

    /** Hands PCM from the glasses (16 kHz mono 16 bit little endian) to the recognizer; dropped when not listening. */
    fun feedPcm(bytes: ByteArray) {
        val out = pipeWrite ?: return
        val writer = pipeWriter ?: return
        pcmBytes += bytes.size
        runCatching { writer.execute { runCatching { out.write(bytes) } } }
    }

    /**
     * Starts listening: with [fromPcm] to what [feedPcm] gets, else through the microphone, routing the audio to the
     * glasses first. False (with [onError]) if it could not start.
     */
    suspend fun start(fromPcm: Boolean = false): Boolean {
        if (recognizer != null) return true
        if (!available) {
            onError("No speech recognizer on this phone")
            return false
        }
        heard.clear()
        pendingPartial = ""
        finishing = false
        done = CompletableDeferred()
        pcmBytes = 0L
        if (fromPcm && !pcmSupported) {
            onError("Camera-stream audio needs Android 13")
            return false
        }
        mic = if (fromPcm) "glasses camera stream (16 kHz)" else routeToBluetoothMic()
        val viaBluetooth = previousMode != null // set while the glasses' hands-free input is the route
        // Selecting the headset is not the audio link being up: without a moment more the first words were lost.
        if (viaBluetooth) delay(SCO_SETTLE_MS)
        onReady(viaBluetooth)
        delay(CaptureFeedback.READY_BEEP_MS + 100) // the beep must have ended, or it is heard as speech
        if (fromPcm) {
            // Only now, so the beep is not in the audio the recognizer gets.
            val (read, write) = ParcelFileDescriptor.createPipe()
            pipeRead = read
            pipeWrite = ParcelFileDescriptor.AutoCloseOutputStream(write)
            pipeWriter = Executors.newSingleThreadExecutor()
        }
        recognizer = SpeechRecognizer.createSpeechRecognizer(context).apply { setRecognitionListener(listener) }
        listen()
        armSilenceTimer(NO_SPEECH_MS)
        return true
    }

    /** Stops listening and returns everything heard since [start] ("" if nothing). */
    suspend fun finish(): String {
        val r = recognizer ?: return heard.joinToString(" ")
        finishing = true
        r.stopListening()
        // The last words arrive in onResults (or onError when nothing more was said). With words already
        // heard as a partial, wait less for the final result: it can come back empty and take seconds.
        withTimeoutOrNull(if (pendingPartial.isNotBlank()) PARTIAL_FINAL_WAIT_MS else FINAL_RESULT_MS) { done?.await() }
        commitPartial()
        release()
        Log.d(TAG, "finish -> '${heard.joinToString(" ").trim()}' (pcmBytes=$pcmBytes)")
        return heard.joinToString(" ").trim()
    }

    /** Stops without a result. */
    fun cancel() {
        recognizer?.cancel()
        release()
        heard.clear()
    }

    private fun listen() {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            // English, whatever the phone's language: with the default (the phone's, e.g. Finnish) the recognizer
            // mixes the two up and returns gibberish. The backend and the spoken replies are English too.
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, LANGUAGE)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, LANGUAGE)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
            // A hint only; many recognizers ignore it, which is why a pause restarts listening.
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, SILENCE_END_MS)
            pipeRead?.let {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, it)
                    putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
                    putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, AudioFormat.ENCODING_PCM_16BIT)
                    putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, PCM_RATE)
                }
            }
        }
        recognizer?.startListening(intent)
    }

    /** Takes the newest partial transcript as heard, when no final result confirmed or replaced it. */
    private fun commitPartial() {
        if (pendingPartial.isNotBlank()) heard += pendingPartial
        pendingPartial = ""
    }

    /** (Re)starts the countdown to [onSilence]; any sign of speech starts it over. */
    private fun armSilenceTimer(afterMs: Long) {
        main.removeCallbacks(silenceTimer)
        main.postDelayed(silenceTimer, afterMs)
    }

    private val listener = object : RecognitionListener {
        override fun onPartialResults(partialResults: Bundle?) {
            val partial = partialResults.best()
            Log.d(TAG, "partial: '$partial'")
            if (partial.isNotBlank()) {
                pendingPartial = partial
                armSilenceTimer(SILENCE_END_MS)
                onPartial((heard + partial).joinToString(" "))
            }
        }

        override fun onResults(results: Bundle?) {
            Log.d(TAG, "results: ${results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)} (finishing=$finishing, keys=${results?.keySet()})")
            val final = results.best()
            if (final.isNotBlank()) {
                heard += final
                pendingPartial = ""
                armSilenceTimer(SILENCE_END_MS)
            } else {
                commitPartial() // an empty final result after words were heard: keep the words
            }
            onPartial(heard.joinToString(" "))
            if (finishing) done?.complete(Unit) else listen() // a pause, not the end: keep listening
        }

        override fun onError(error: Int) {
            Log.d(TAG, "error $error (finishing=$finishing)")
            val silence = error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT
            if (finishing || silence) commitPartial()
            when {
                finishing -> done?.complete(Unit)
                silence -> listen()
                // Restarting right after a result can find the recognizer still busy: try again shortly.
                error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY && busyRetries++ < 3 ->
                    main.postDelayed({ if (recognizer != null && !finishing) listen() }, 250)
                else -> {
                    Log.w(TAG, "recognizer error $error")
                    onError(describe(error))
                    done?.complete(Unit)
                }
            }
        }

        override fun onReadyForSpeech(params: Bundle?) {
            busyRetries = 0
        }
        // Something that sounds like speech: give it time to turn into words (noise never does, and ends here).
        override fun onBeginningOfSpeech() = armSilenceTimer(maxOf(SILENCE_END_MS, SPEECH_START_MS))
        override fun onRmsChanged(rmsdB: Float) = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEndOfSpeech() = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    /** Sets the glasses' hands-free input as the communication device and waits for the link to come up. */
    private suspend fun routeToBluetoothMic(): String {
        val sco = audio.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO }
            ?: return "phone microphone"
        previousMode = audio.mode
        audio.mode = AudioManager.MODE_IN_COMMUNICATION
        if (!audio.setCommunicationDevice(sco)) {
            restoreAudio()
            return "phone microphone (Bluetooth refused)"
        }
        // The SCO link takes a moment; words said before it is up would come from the phone.
        withTimeoutOrNull(SCO_WAIT_MS) {
            while (audio.communicationDevice?.id != sco.id) delay(50)
        }
        return sco.productName?.toString()?.ifBlank { null } ?: "Bluetooth headset"
    }

    private fun restoreAudio() {
        previousMode?.let {
            audio.clearCommunicationDevice()
            audio.mode = it
        }
        previousMode = null
    }

    private fun release() {
        main.removeCallbacksAndMessages(null)
        recognizer?.destroy()
        recognizer = null
        pipeWriter?.shutdownNow()
        pipeWriter = null
        runCatching { pipeWrite?.close() }
        runCatching { pipeRead?.close() }
        pipeWrite = null
        pipeRead = null
        finishing = false
        done?.complete(Unit)
        done = null
        restoreAudio()
    }

    private fun Bundle?.best(): String =
        this?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()

    private fun describe(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone permission missing"
        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Speech recognition needs a network"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Speech recognizer busy"
        SpeechRecognizer.ERROR_AUDIO -> "Microphone error"
        else -> "Speech recognition failed ($error)"
    }

    companion object {
        /** A message ends this long after the last words. */
        const val SILENCE_END_MS = 1_500L
        /** ...or this long after the microphone opened, if nothing was said. */
        const val NO_SPEECH_MS = 8_000L
        /** What is recognized (BCP 47). */
        const val LANGUAGE = "en-US"
        private const val TAG = "VoiceInput"
        private const val FINAL_RESULT_MS = 4_000L
        private const val PARTIAL_FINAL_WAIT_MS = 1_200L
        private const val SCO_WAIT_MS = 1_500L
        private const val SPEECH_START_MS = 4_000L
        private const val SCO_SETTLE_MS = 400L
        /** The sample rate asked of the glasses' camera stream (AudioSampleRate.RATE_16000). */
        const val PCM_RATE = 16_000
    }
}
