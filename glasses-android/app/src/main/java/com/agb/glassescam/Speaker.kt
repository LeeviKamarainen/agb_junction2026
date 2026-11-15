package com.agb.glassescam

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.audiofx.LoudnessEnhancer
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale

/**
 * Reads the backend's replies aloud with Android's own text to speech, on the phone. The glasses are a
 * Bluetooth headset to the phone, so what the phone plays comes out of their speakers; nothing is
 * generated on the backend and no network is needed.
 *
 * It is made loud on purpose, since the glasses' speakers are small: while it talks the media volume is raised
 * to the maximum (and put back afterwards), and a [LoudnessEnhancer] adds [GAIN_MB] of gain on top.
 *
 * A new [speak] replaces whatever is still being read. Other audio (music) is ducked while it talks.
 * [awaitDone] lets the caller wait until the speech has ended, which matters before the glasses' microphone
 * opens again: otherwise it would hear the reply. The engine starts asynchronously, so text spoken before it
 * is ready waits for it; if it never starts, [speak] does nothing.
 */
class Speaker(context: Context) {

    private val audio = context.getSystemService(AudioManager::class.java)
    private val attributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANT)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()
    private val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        .setAudioAttributes(attributes)
        .build()

    // TextToSpeech plays on this audio session, which is what the loudness boost is attached to.
    private val sessionId = audio.generateAudioSessionId()
    private var booster: LoudnessEnhancer? = null
    private var savedVolume: Int? = null // the media volume before it was raised, put back when the speech ends

    private val lock = Any()
    private var tts: TextToSpeech? = null
    private var ready = false
    private var failed = false
    private var pending: String? = null // spoken as soon as the engine is ready
    private var latest = 0 // id of the newest utterance; callbacks about older ones are ignored

    /** True from [speak] until the speech ends, is replaced or is stopped. */
    private val speaking = MutableStateFlow(false)

    init {
        // Under the lock so the start-up callback cannot run before [tts] is assigned.
        synchronized(lock) {
            tts = TextToSpeech(context.applicationContext) { status -> onInit(status) }
        }
    }

    private fun onInit(status: Int) {
        synchronized(lock) {
            val engine = tts
            if (status != TextToSpeech.SUCCESS || engine == null) {
                Log.w(TAG, "text to speech failed to start ($status)")
                failed = true
                pending = null
                speaking.value = false
                engine?.shutdown() // do not keep the engine's service bound
                tts = null
                return
            }
            engine.setAudioAttributes(attributes)
            // The backend replies in English; a phone set to another language would mangle it.
            val language = engine.setLanguage(Locale.ENGLISH)
            if (language == TextToSpeech.LANG_MISSING_DATA || language == TextToSpeech.LANG_NOT_SUPPORTED) {
                Log.w(TAG, "no English voice installed, using the phone's default")
            }
            engine.setOnUtteranceProgressListener(listener)
            booster = runCatching {
                LoudnessEnhancer(sessionId).apply {
                    setTargetGain(GAIN_MB)
                    enabled = true
                }
            }.onFailure { Log.w(TAG, "no loudness boost", it) }.getOrNull()
            ready = true
            pending?.let { start(it) }
            pending = null
        }
    }

    /** Reads [text] aloud, replacing anything still being read. */
    fun speak(text: String) {
        val clean = text.trim().take(MAX_CHARS)
        if (clean.isEmpty()) return
        synchronized(lock) {
            if (failed || tts == null) return
            latest++ // whatever was being read is now stale
            speaking.value = true
            if (ready) start(clean) else pending = clean
        }
    }

    /** Stops reading at once. */
    fun stop() {
        synchronized(lock) {
            latest++
            pending = null
            tts?.stop()
            end()
        }
    }

    /**
     * Waits until nothing is being read (at most [timeoutMs]); returns at once when it is already quiet.
     * True if something was being read, so the caller can leave the Bluetooth link a moment to play its tail.
     */
    suspend fun awaitDone(timeoutMs: Long = AWAIT_MS): Boolean {
        if (!speaking.value) return false
        withTimeoutOrNull(timeoutMs) { speaking.first { !it } }
        return true
    }

    fun release() {
        synchronized(lock) {
            latest++
            pending = null
            tts?.shutdown()
            tts = null
            ready = false
            booster?.release()
            booster = null
            end()
        }
    }

    // Callers hold [lock].
    private fun start(text: String) {
        val t = tts ?: return end()
        audio.requestAudioFocus(focus)
        if (savedVolume == null) {
            savedVolume = audio.getStreamVolume(AudioManager.STREAM_MUSIC)
            audio.setStreamVolume(AudioManager.STREAM_MUSIC, audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC), 0)
        }
        val params = Bundle().apply {
            putInt(TextToSpeech.Engine.KEY_PARAM_SESSION_ID, sessionId)
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1f)
        }
        val result = t.speak(text, TextToSpeech.QUEUE_FLUSH, params, "reply-$latest")
        if (result != TextToSpeech.SUCCESS) {
            Log.w(TAG, "text to speech refused the text ($result)")
            end()
        }
    }

    private fun end() {
        savedVolume?.let { audio.setStreamVolume(AudioManager.STREAM_MUSIC, it, 0) }
        savedVolume = null
        audio.abandonAudioFocusRequest(focus)
        speaking.value = false
    }

    private val listener = object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) = Unit
        override fun onDone(utteranceId: String?) = finished(utteranceId)
        override fun onStop(utteranceId: String?, interrupted: Boolean) = finished(utteranceId)

        // Deprecated in the framework, but abstract: the two-argument version below is the one newer Android calls
        @Suppress("OVERRIDE_DEPRECATION")
        override fun onError(utteranceId: String?) = finished(utteranceId)
        override fun onError(utteranceId: String?, errorCode: Int) = finished(utteranceId)
    }

    /** Only the newest utterance ends the speech: an older one being cut off by a new one must not. */
    private fun finished(utteranceId: String?) {
        synchronized(lock) {
            if (utteranceId == "reply-$latest") end()
        }
    }

    private companion object {
        const val TAG = "Speaker"
        const val GAIN_MB = 1_000 // +10 dB on top of the maximum volume; much more starts to distort speech
        const val MAX_CHARS = 500 // about 35 s of speech, far longer than a reply; the engine refuses ~4000
        const val AWAIT_MS = 45_000L // longer than MAX_CHARS takes; a stuck engine must not keep the mic closed
    }
}
