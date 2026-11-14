package com.agb.glassescam

import org.json.JSONObject

/** Anything unknown is drawn as a reply. */
enum class MessageType { TEXT_REPLY, TRIGGER }

/** [speech] is what the backend wants said aloud, when it sent something other than the screen's text. */
data class GlassesMessage(
    val type: MessageType,
    val title: String,
    val text: String,
    val speech: String? = null,
) {
    /** What to read aloud: the backend's `speech`, else a trigger's title and text, else the text. */
    val spoken: String
        get() = speech?.takeIf { it.isNotBlank() }
            ?: if (type == MessageType.TRIGGER && title.isNotBlank() && title != text) "$title. $text" else text
}

sealed interface FrameOutcome {
    data class Ok(val stored: Boolean, val note: String?, val message: GlassesMessage?) : FrameOutcome
    data class Fail(val message: String) : FrameOutcome
}

/** The answer to a voice command: what to show ([message], null when the reply has no text), and
 * [listenQuestion] when the backend wants the wearer's next words as a question about their photos. */
sealed interface CommandOutcome {
    data class Ok(val message: GlassesMessage?, val listenQuestion: Boolean) : CommandOutcome
    data class Fail(val message: String) : CommandOutcome
}

internal fun parseCommandReply(reply: JSONObject): CommandOutcome.Ok =
    CommandOutcome.Ok(
        message = parsePushedReply(reply),
        listenQuestion = reply.optString("client_action") == "listen_question",
    )

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
        // org.json's optString turns a JSON null into the string "null"
        speech = if (reply.isNull("speech")) null else reply.optString("speech"),
    )
}
