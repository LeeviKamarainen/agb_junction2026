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
    }

    private fun buzz(ms: Long) {
        vibrator?.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
    }
}
