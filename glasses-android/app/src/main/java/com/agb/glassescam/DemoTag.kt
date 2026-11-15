package com.agb.glassescam

/**
 * A demo's tag as the backend stores it (its memory `collection`: 1 to 40 of a-z, 0-9, _ and -, starting with a
 * letter or digit): trimmed, lower case, spaces turned into dashes, anything else dropped. Null when nothing is
 * left, so no demo can start without a tag.
 */
internal fun cleanDemoTag(raw: String): String? =
    raw.trim().lowercase()
        .replace(Regex("""\s+"""), "-")
        .filter { it in 'a'..'z' || it in '0'..'9' || it == '_' || it == '-' }
        .trimStart('-', '_')
        .take(40)
        .ifEmpty { null }
