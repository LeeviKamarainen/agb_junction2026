package com.agb.glassescam

import org.json.JSONObject

/** Anything unknown is drawn as a reply. */
enum class MessageType { TEXT_REPLY, TRIGGER }

data class GlassesMessage(val type: MessageType, val title: String, val text: String)

sealed interface FrameOutcome {
    data class Ok(val stored: Boolean, val note: String?, val message: GlassesMessage?) : FrameOutcome
    data class Fail(val message: String) : FrameOutcome
}

internal fun parseMemoryResult(json: JSONObject): FrameOutcome.Ok {
    val message = json.optJSONObject("message")?.let {
        GlassesMessage(
            type = if (it.optString("type") == "trigger") MessageType.TRIGGER else MessageType.TEXT_REPLY,
            title = it.optString("title"),
            text = it.optString("text"),
        )
    }
    return FrameOutcome.Ok(
        stored = json.getBoolean("stored"),
        note = if (json.isNull("note")) null else json.optString("note"),
        message = message,
    )
}

/** Pushed GlassesReply warnings use the display tree, rather than the memory message shape. */
internal fun parsePushedReply(reply: JSONObject): GlassesMessage? {
    val texts = mutableListOf<String>()
    fun visit(node: JSONObject) {
        if (node.optString("type") == "text") texts += node.optString("value")
        node.optJSONArray("children")?.let { children ->
            for (i in 0 until children.length()) children.optJSONObject(i)?.let { visit(it) }
        }
    }
    reply.optJSONObject("screen")?.let { visit(it) }
    if (texts.isEmpty()) return null
    return GlassesMessage(
        type = if (reply.optString("intent") == "warning") MessageType.TRIGGER else MessageType.TEXT_REPLY,
        title = texts.first(),
        text = texts.drop(1).joinToString("\n").ifBlank { texts.first() },
    )
}
