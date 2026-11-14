package com.agb.glassescam

import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.Image
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.util.Log
import java.io.ByteArrayOutputStream

/**
 * Decodes the glasses' HEVC video stream into raw frames (no screen needed) and, when asked,
 * turns the NEXT decoded frame into a JPEG. This is the fast "snapshot from the stream" path:
 * a stream frame is only tens of KB, unlike a full still photo (about 1.4 MB over a slow link).
 */
class SnapshotDecoder(width: Int, height: Int) {

    private val codec: MediaCodec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_HEVC)
    private val info = MediaCodec.BufferInfo()

    @Volatile
    private var pending: ((ByteArray) -> Unit)? = null

    val hasPending: Boolean get() = pending != null

    init {
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_HEVC, width, height)
        format.setInteger(
            MediaFormat.KEY_COLOR_FORMAT,
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible,
        )
        codec.configure(format, null, null, 0) // no Surface: we read the decoded frames ourselves
        codec.start()
    }

    /** The next decoded frame is converted to a JPEG and passed to [onJpeg] (on the decoder thread). */
    fun requestSnapshot(onJpeg: (ByteArray) -> Unit) {
        pending = onJpeg
    }

    fun cancelSnapshot() {
        pending = null
    }

    fun decode(data: ByteArray, presentationTimeUs: Long, isCodecConfig: Boolean) {
        try {
            val index = codec.dequeueInputBuffer(10_000)
            if (index >= 0) {
                codec.getInputBuffer(index)?.let { buf ->
                    buf.clear()
                    buf.put(data)
                }
                val flags = if (isCodecConfig) MediaCodec.BUFFER_FLAG_CODEC_CONFIG else 0
                codec.queueInputBuffer(index, 0, data.size, presentationTimeUs, flags)
            }
            drainOutput()
        } catch (e: Exception) {
            Log.w(TAG, "decode failed", e)
        }
    }

    private fun drainOutput() {
        while (true) {
            val out = codec.dequeueOutputBuffer(info, 0)
            when {
                out >= 0 -> {
                    val callback = pending
                    if (callback != null && info.size > 0 &&
                        (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0
                    ) {
                        pending = null
                        val image = codec.getOutputImage(out)
                        if (image != null) {
                            try {
                                callback(image.toJpeg())
                            } catch (e: Exception) {
                                Log.w(TAG, "snapshot conversion failed", e)
                            } finally {
                                image.close()
                            }
                        }
                    }
                    codec.releaseOutputBuffer(out, false)
                }
                out == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED ||
                    out == MediaCodec.INFO_OUTPUT_BUFFERS_CHANGED -> continue
                else -> return
            }
        }
    }

    fun release() {
        try {
            codec.stop()
        } catch (_: Exception) {
        }
        codec.release()
    }

    /** YUV_420_888 (any plane layout) -> NV21 -> JPEG, cropped to the visible area. */
    private fun Image.toJpeg(): ByteArray {
        val w = width
        val h = height
        val ySize = w * h
        val nv21 = ByteArray(ySize + w * h / 2)

        val yPlane = planes[0]
        val yBuf = yPlane.buffer
        for (row in 0 until h) {
            val rowStart = row * yPlane.rowStride
            for (col in 0 until w) {
                nv21[row * w + col] = yBuf.get(rowStart + col * yPlane.pixelStride)
            }
        }

        val uPlane = planes[1]
        val vPlane = planes[2]
        val uBuf = uPlane.buffer
        val vBuf = vPlane.buffer
        for (row in 0 until h / 2) {
            for (col in 0 until w / 2) {
                val uIndex = row * uPlane.rowStride + col * uPlane.pixelStride
                val vIndex = row * vPlane.rowStride + col * vPlane.pixelStride
                val out = ySize + row * w + col * 2
                nv21[out] = vBuf.get(vIndex) // NV21 is V then U
                nv21[out + 1] = uBuf.get(uIndex)
            }
        }

        val crop = cropRect.takeIf { !it.isEmpty } ?: Rect(0, 0, w, h)
        val jpeg = ByteArrayOutputStream()
        YuvImage(nv21, ImageFormat.NV21, w, h, null).compressToJpeg(crop, 92, jpeg)
        return jpeg.toByteArray()
    }

    private companion object {
        const val TAG = "SnapshotDecoder"
    }
}
