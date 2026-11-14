package com.agb.glassescam

import android.content.Context
import android.content.Intent
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Push-to-talk speech to text: Android's own speech recognizer, listening through the glasses'
 * microphone. The glasses are a Bluetooth headset to the phone, so for the recording the phone's
 * communication device is set to their Bluetooth SCO (hands-free) input; without one the phone's own
 * microphone is used, and [mic] says which.
 *
 * The recognizer stops by itself when the wearer pauses. Until [finish] is called that pause is not the
 * end: what was heard so far is kept and listening starts again, so the button decides when a message
 * ends. Use from the main thread only (SpeechRecognizer requires it).
 */
class VoiceInput(
    private val context: Context,
    private val onPartial: (String) -> Unit,
    private val onError: (String) -> Unit,
) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private var recognizer: SpeechRecognizer? = null
    private val heard = mutableListOf<String>() // finished pieces of this message
    private var finishing = false
    private var done: CompletableDeferred<Unit>? = null
    private var previousMode: Int? = null
    private var busyRetries = 0
    private val main = Handler(Looper.getMainLooper())

    /** What is recording: the glasses' name, or "phone microphone". */
    var mic: String = ""
        private set

    val available: Boolean get() = SpeechRecognizer.isRecognitionAvailable(context)

    /** Routes audio to the glasses and starts listening. False (with [onError]) if it could not start. */
    suspend fun start(): Boolean {
        if (recognizer != null) return true
        if (!available) {
            onError("No speech recognizer on this phone")
            return false
        }
        heard.clear()
        finishing = false
        done = CompletableDeferred()
        mic = routeToBluetoothMic()
        recognizer = SpeechRecognizer.createSpeechRecognizer(context).apply { setRecognitionListener(listener) }
        listen()
        return true
    }

    /** Stops listening and returns everything heard since [start] ("" if nothing). */
    suspend fun finish(): String {
        val r = recognizer ?: return heard.joinToString(" ")
        finishing = true
        r.stopListening()
        // The last words arrive in onResults (or onError when nothing more was said).
        withTimeoutOrNull(FINAL_RESULT_MS) { done?.await() }
        release()
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
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
            // A hint only; many recognizers ignore it, which is why a pause restarts listening.
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 3_000L)
        }
        recognizer?.startListening(intent)
    }

    private val listener = object : RecognitionListener {
        override fun onPartialResults(partialResults: Bundle?) {
            val partial = partialResults.best()
            if (partial.isNotBlank()) onPartial((heard + partial).joinToString(" "))
        }

        override fun onResults(results: Bundle?) {
            results.best().takeIf { it.isNotBlank() }?.let { heard += it }
            onPartial(heard.joinToString(" "))
            if (finishing) done?.complete(Unit) else listen() // a pause, not the end: keep listening
        }

        override fun onError(error: Int) {
            val silence = error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT
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
        override fun onBeginningOfSpeech() = Unit
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

    private companion object {
        const val TAG = "VoiceInput"
        const val FINAL_RESULT_MS = 4_000L
        const val SCO_WAIT_MS = 1_500L
    }
}
