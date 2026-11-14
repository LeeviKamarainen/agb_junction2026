package com.agb.glassescam

import android.content.Context

/**
 * Where the wearer's "straight ahead" lands in the camera picture.
 *
 * The camera sits on the frame, not at the eye, so the middle of the photo is not what the wearer is looking
 * at. This stores the spot (as a fraction of the photo: 0..1 from the left / top) that corresponds to the
 * middle of the glasses, found by calibrating on a target at a typical distance. It is a fixed 2D offset: it
 * is exact only near the distance it was calibrated at (parallax grows for closer objects).
 */
object EyeCalibration {

    private const val PREFS = "eye_calibration"

    /** (fx, fy); defaults to the middle of the photo. */
    fun get(context: Context): Pair<Float, Float> {
        val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return p.getFloat("fx", 0.5f) to p.getFloat("fy", 0.5f)
    }

    fun set(context: Context, fx: Float, fy: Float) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putFloat("fx", fx.coerceIn(0f, 1f))
            .putFloat("fy", fy.coerceIn(0f, 1f))
            .apply()
    }

    fun reset(context: Context) = set(context, 0.5f, 0.5f)
}
