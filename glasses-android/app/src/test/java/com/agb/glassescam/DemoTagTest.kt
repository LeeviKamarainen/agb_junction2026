package com.agb.glassescam

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DemoTagTest {
    @Test fun `a tag is made lower case with dashes for spaces`() {
        assertEquals("demo-kitchen", cleanDemoTag("  Demo Kitchen "))
    }

    @Test fun `characters the backend refuses are dropped`() {
        assertEquals("demo-1_a", cleanDemoTag("demo-1_a!?/ä"))
    }

    @Test fun `a tag must start with a letter or digit and fit 40 characters`() {
        assertEquals("a", cleanDemoTag("-_a"))
        assertEquals(40, cleanDemoTag("x".repeat(60))!!.length)
    }

    @Test fun `nothing left means no tag`() {
        assertNull(cleanDemoTag("  "))
        assertNull(cleanDemoTag("!!--__"))
    }
}
