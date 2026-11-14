package com.agb.glassescam

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** What a reply makes the phone say aloud: [GlassesMessage.spoken]. */
class FrameApiTest {

    private fun reply(speech: String?, vararg texts: String, intent: String = "ask"): JSONObject {
        val children = texts.joinToString(",") { """{"type":"text","value":"$it"}""" }
        val speechJson = if (speech == null) "null" else "\"$speech\""
        return JSONObject(
            """{"intent":"$intent","speech":$speechJson,"screen":{"type":"column","children":[$children]}}""",
        )
    }

    @Test
    fun theBackendsSpeechIsReadInsteadOfTheScreen() {
        val message = parsePushedReply(reply("Careful: stool ahead.", "Watch out", "Stool"))!!
        assertEquals("Careful: stool ahead.", message.spoken)
    }

    @Test
    fun aJsonNullSpeechIsNotReadAsTheWordNull() {
        val message = parsePushedReply(reply(null, "On the desk"))!!
        assertNull(message.speech)
        assertEquals("On the desk", message.spoken)
    }

    @Test
    fun aTriggerWithoutSpeechReadsItsTitleThenItsText() {
        val message = parsePushedReply(reply(null, "Keys", "Take your keys", intent = "warning"))!!
        assertEquals("Keys. Take your keys", message.spoken)
    }

    @Test
    fun aTriggerWhoseTitleIsItsTextIsReadOnce() {
        val message = parsePushedReply(reply(null, "Stool ahead", intent = "warning"))!!
        assertEquals("Stool ahead", message.spoken)
    }

    @Test
    fun aMemoryMessageReadsItsTextOrItsTitleAndText() {
        val trigger = parseMemoryResult(
            JSONObject("""{"stored":true,"message":{"type":"trigger","title":"Keys","text":"Take your keys"}}"""),
        ).message!!
        assertEquals("Keys. Take your keys", trigger.spoken)

        val answer = parseMemoryResult(
            JSONObject("""{"stored":true,"message":{"type":"text_reply","title":"Memory","text":"By the door."}}"""),
        ).message!!
        assertEquals("By the door.", answer.spoken)
    }
}
