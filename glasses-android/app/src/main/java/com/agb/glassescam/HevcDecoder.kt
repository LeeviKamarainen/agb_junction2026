package com.agb.glassescam

import android.media.MediaCodec
import android.media.MediaFormat
import android.util.Log
import android.view.Surface

/**
 * Decodes the HEVC (H.265) frames the glasses stream and renders them straight to a [Surface].
 *
 * The DAT SDK delivers compressed frames when StreamConfiguration(compressVideo = true).
 * Parameter sets (VPS/SPS/PPS) arrive as frames flagged isCodecConfig and/or in-band, so we just
 * feed everything in order.
 */
class HevcDecoder(surface: Surface, width: Int, height: Int) {

    private val codec: MediaCodec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_HEVC)
    private val info = MediaCodec.BufferInfo()

    init {
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_HEVC, width, height)
        codec.configure(format, surface, null, 0)
        codec.start()
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
                out >= 0 -> codec.releaseOutputBuffer(out, true) // true = render to surface
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

    private companion object {
        const val TAG = "HevcDecoder"
    }
}
