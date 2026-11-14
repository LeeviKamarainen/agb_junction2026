package com.agb.glassescam

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorMatrixColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.Rect
import android.graphics.RectF

/**
 * Draws the picture that is pushed to the glasses display in live-segment mode: a 600x600 image (the display's
 * native size) on a black background (on an additive see-through display, black should show as clear) with
 *  - the cut-out of the segmented object (the camera frame where the mask is white),
 *  - its upright box (green) and rotated box (yellow),
 *  - a cyan crosshair at the calibrated eye point = the spot where the object should end up,
 *  - a short status line.
 */
object LiveRenderer {

    const val SIZE = 600

    // Mask (white = object) -> alpha, so DST_IN keeps only the object's pixels.
    private val MASK_TO_ALPHA = floatArrayOf(
        0f, 0f, 0f, 0f, 0f,
        0f, 0f, 0f, 0f, 0f,
        0f, 0f, 0f, 0f, 0f,
        0.33f, 0.33f, 0.33f, 0f, 0f,
    )

    // Mask (white = object) -> translucent green, to highlight the object on top of the camera frame.
    private val MASK_TO_GREEN = floatArrayOf(
        0f, 0f, 0f, 0f, 0f,
        0f, 0f, 0f, 0f, 255f,
        0f, 0f, 0f, 0f, 0f,
        0.12f, 0.12f, 0.12f, 0f, 0f,
    )

    /**
     * @param frameW / [frameH] size of the frame that was sent to the API (box coordinates are in these pixels)
     * @param result null when there is no object at the point or the API call failed
     */
    fun compose(
        frameJpeg: ByteArray,
        frameW: Int,
        frameH: Int,
        result: SegmentResult?,
        eyeFx: Float,
        eyeFy: Float,
        label: String,
        showFrame: Boolean = false, // true: draw the full (dimmed) camera frame and tint the object, not just the cut-out
        showCrosshair: Boolean = true,
    ): Bitmap {
        val out = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        canvas.drawColor(Color.BLACK)

        // Fit the whole frame inside the square display.
        val scale = minOf(SIZE / frameW.toFloat(), SIZE / frameH.toFloat())
        val dw = frameW * scale
        val dh = frameH * scale
        val ox = (SIZE - dw) / 2f
        val oy = (SIZE - dh) / 2f

        // Decode smaller than the original to keep this fast (about once per second on the phone).
        var sample = 1
        while (maxOf(frameW, frameH) / (sample * 2) >= SIZE) sample *= 2
        val opts = BitmapFactory.Options().apply { inSampleSize = sample }

        val mask = result?.maskPng?.let { BitmapFactory.decodeByteArray(it, 0, it.size, opts) }
        val frame = if (showFrame || mask != null) {
            BitmapFactory.decodeByteArray(frameJpeg, 0, frameJpeg.size, opts)
        } else {
            null
        }
        val dest = RectF(ox, oy, ox + dw, oy + dh)
        if (frame != null && showFrame) {
            // Phone view: the whole camera frame, dimmed, so you can see what the glasses see.
            canvas.drawBitmap(frame, null, dest, Paint(Paint.FILTER_BITMAP_FLAG).apply { alpha = 110 })
        }
        if (frame != null && mask != null) {
            val cut = Bitmap.createBitmap(frame.width, frame.height, Bitmap.Config.ARGB_8888)
            val c = Canvas(cut)
            c.drawBitmap(frame, 0f, 0f, null)
            val keepObject = Paint().apply {
                xfermode = PorterDuffXfermode(PorterDuff.Mode.DST_IN)
                colorFilter = ColorMatrixColorFilter(MASK_TO_ALPHA)
            }
            c.drawBitmap(mask, null, Rect(0, 0, frame.width, frame.height), keepObject)
            canvas.drawBitmap(cut, null, dest, Paint(Paint.FILTER_BITMAP_FLAG))
            cut.recycle()
            if (showFrame) {
                // The full camera frame is visible, so also tint the object green: it stands out clearly
                // against the dimmed background even where its colours are similar.
                val tint = Paint(Paint.FILTER_BITMAP_FLAG).apply { colorFilter = ColorMatrixColorFilter(MASK_TO_GREEN) }
                canvas.drawBitmap(mask, null, dest, tint)
            }
        }
        frame?.recycle()
        mask?.recycle()

        val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }

        result?.let { r ->
            if (r.box.size == 4) {
                stroke.color = Color.GREEN
                stroke.strokeWidth = 3f
                canvas.drawRect(
                    ox + r.box[0] * scale, oy + r.box[1] * scale,
                    ox + r.box[2] * scale, oy + r.box[3] * scale,
                    stroke,
                )
            }
            if (r.rotatedBox.size == 4) {
                stroke.color = Color.YELLOW
                stroke.strokeWidth = 2f
                val path = Path()
                r.rotatedBox.forEachIndexed { i, (x, y) ->
                    val px = ox + x * scale
                    val py = oy + y * scale
                    if (i == 0) path.moveTo(px, py) else path.lineTo(px, py)
                }
                path.close()
                canvas.drawPath(path, stroke)
            }
        }

        // Target: where the wearer is looking straight ahead; put the object here. Can be switched off in the app.
        if (showCrosshair) {
            val ex = ox + eyeFx * dw
            val ey = oy + eyeFy * dh
            stroke.color = Color.CYAN
            stroke.strokeWidth = 3f
            canvas.drawCircle(ex, ey, 22f, stroke)
            canvas.drawLine(ex - 40f, ey, ex - 22f, ey, stroke)
            canvas.drawLine(ex + 22f, ey, ex + 40f, ey, stroke)
            canvas.drawLine(ex, ey - 40f, ex, ey - 22f, stroke)
            canvas.drawLine(ex, ey + 22f, ex, ey + 40f, stroke)
        }

        val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            textSize = 24f
        }
        canvas.drawText(label.take(34), 12f, SIZE - 14f, text)
        return out
    }
}
