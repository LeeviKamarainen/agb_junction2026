package com.agb.glassescam

import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.VibrationEffect
import android.os.VibratorManager

/**
 * Phone-side haptic + sound cues for the capture flow, useful when you're wearing glasses that
 * have no screen (or aren't looking at the phone): a short buzz and beep when a capture starts,
 * a longer buzz and confirmation tone when the photo is saved, and a low tone on failure.
 */
class CaptureFeedback(context: Context) {

    private val vibrator = context.getSystemService(VibratorManager::class.java)?.defaultVibrator
    private val tone: ToneGenerator? = try {
        ToneGenerator(AudioManager.STREAM_NOTIFICATION, 70)
    } catch (_: Exception) {
        null
    }

    // The same beep on the call stream, which is the one that follows the Bluetooth hands-free route to the
    // glasses while their microphone is in use (the notification stream plays from the phone).
    private val callTone: ToneGenerator? = try {
        ToneGenerator(AudioManager.STREAM_VOICE_CALL, 100)
    } catch (_: Exception) {
        null
    }

    /** A press on the glasses was understood: a short buzz on the phone, no sound (the beep says "speak now"). */
    fun pressed() = buzz(40)

    /**
     * The microphone is open: speak now. A beep (through the glasses when [viaBluetooth], their microphone being
     * in use) and a buzz. The caller waits [READY_BEEP_MS] before listening so the beep is not heard as speech.
     */
    fun ready(viaBluetooth: Boolean) {
        buzz(60)
        (if (viaBluetooth) callTone else tone)?.startTone(ToneGenerator.TONE_PROP_BEEP2, READY_BEEP_MS.toInt())
    }

    fun started() {
        buzz(60)
        tone?.startTone(ToneGenerator.TONE_PROP_BEEP, 80)
    }

    fun done() {
        buzz(160)
        tone?.startTone(ToneGenerator.TONE_PROP_ACK, 200)
    }

    fun failed() {
        buzz(300)
        tone?.startTone(ToneGenerator.TONE_PROP_NACK, 300)
    }

    fun release() {
        tone?.release()
        callTone?.release()
    }

    companion object {
        /** How long the "speak now" beep lasts. */
        const val READY_BEEP_MS = 150L
    }

    private fun buzz(ms: Long) {
        vibrator?.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
    }
}
