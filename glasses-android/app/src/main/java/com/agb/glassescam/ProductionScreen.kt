package com.agb.glassescam

import android.os.SystemClock
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp

/** Taps in the hidden corner needed to open the developer options, and how close together they must be. */
private const val DEVELOPER_TAPS = 3
private const val DEVELOPER_TAP_WINDOW_MS = 1_500L

/**
 * The screen of production mode: what the glasses are doing, in one glance, and nothing to configure. The
 * app does the rest by itself (see GlassesViewModel.superviseProduction). The developer options open from an
 * invisible button in the top right corner: tap it [DEVELOPER_TAPS] times.
 */
@Composable
fun ProductionScreen(
    ui: UiState,
    onRegister: () -> Unit,
    onRetry: () -> Unit,
    onTalk: () -> Unit,
    onDismissMessage: () -> Unit,
    onOpenDeveloper: () -> Unit,
    onDemoTag: (String) -> Unit,
    onToggleDemo: () -> Unit,
) {
    val notRegistered = ui.initialized && ui.deviceCount == 0 && !ui.mockEnabled
    val (headline, detail, color) = when {
        !ui.initialized -> Triple("Starting...", "Allow Bluetooth to continue.", Color(0xFFFFF3CD))
        notRegistered ->
            Triple("No glasses registered", "Register them with the Meta AI app, then they connect by themselves.", Color(0xFFFFF3CD))
        ui.voiceRecording -> Triple("Listening...", ui.voiceText.ifBlank { "Speak now" }, Color(0xFFF8D7DA))
        ui.voiceSending -> Triple("Thinking...", ui.voiceText, Color(0xFFD6E9FF))
        ui.memoryArmed && ui.memoryStreaming ->
            Triple("Streaming", "Press the capture button on the glasses to talk.", Color(0xFFD4EDDA))
        ui.memoryArmed -> Triple("Connected", ui.memoryStatus, Color(0xFFD4EDDA))
        else -> Triple("Connecting to the glasses...", ui.productionNote, Color(0xFFFFF3CD))
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.statusBarsPadding().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Glasses memory", style = MaterialTheme.typography.headlineSmall)

            Column(
                modifier = Modifier.fillMaxWidth().background(color).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(headline, style = MaterialTheme.typography.titleLarge, color = Color.Black)
                if (detail.isNotBlank()) Text(detail, color = Color.Black)
            }

            if (notRegistered) {
                Button(onClick = onRegister, modifier = Modifier.fillMaxWidth()) { Text("Register glasses") }
            }
            if (ui.productionBlocked) {
                Button(onClick = onRetry, modifier = Modifier.fillMaxWidth()) { Text("Retry") }
            }
            if (ui.memoryArmed) {
                // The phone's own button for what the glasses' button does.
                OutlinedButton(
                    onClick = onTalk,
                    enabled = !ui.voiceSending,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (ui.voiceRecording) "Stop and send" else "Talk") }
                Text("Backend: ${ui.memoryConnection}", style = MaterialTheme.typography.bodySmall)
                Text(ui.memoryStatus, style = MaterialTheme.typography.bodySmall)
            } else if (ui.productionBlocked || ui.productionNote.isNotBlank() && !notRegistered) {
                Text(ui.productionNote, style = MaterialTheme.typography.bodyMedium)
            }
            if (ui.memoryArmed) {
                // Demo recording: the stream's frames are stored in the backend under this tag.
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Demo recording", style = MaterialTheme.typography.titleMedium)
                        OutlinedTextField(
                            value = ui.demoTag,
                            onValueChange = onDemoTag,
                            label = { Text("Tag (stored as the memories' collection)") },
                            singleLine = true,
                            enabled = !ui.demoRecording,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Button(
                            onClick = onToggleDemo,
                            enabled = ui.demoRecording || (ui.memoryStreaming && cleanDemoTag(ui.demoTag) != null),
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(if (ui.demoRecording) "Stop demo" else "Record demo") }
                        if (ui.demoRecording) {
                            Text(
                                "Recording '${ui.demoTag}': ${ui.demoStored} stills stored, ${ui.demoSent} frames sent",
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
            }
            if (ui.message.isNotBlank()) Text(ui.message, style = MaterialTheme.typography.bodySmall)

            ui.glassesMessage?.let { message ->
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        val kind = if (message.type == MessageType.TRIGGER) "Trigger" else "Reply"
                        if (message.title.isNotBlank()) Text("$kind: ${message.title}", style = MaterialTheme.typography.titleSmall)
                        Text(message.text)
                        TextButton(onClick = onDismissMessage) { Text("Dismiss") }
                    }
                }
            }
        }

        // The hidden developer button: invisible, top right corner, three quick taps.
        var taps by remember { mutableIntStateOf(0) }
        var lastTapMs by remember { mutableLongStateOf(0L) }
        Box(
            modifier = Modifier
                .align(Alignment.TopEnd)
                .statusBarsPadding()
                .size(72.dp)
                .pointerInput(Unit) {
                    detectTapGestures {
                        val now = SystemClock.elapsedRealtime()
                        taps = if (now - lastTapMs < DEVELOPER_TAP_WINDOW_MS) taps + 1 else 1
                        lastTapMs = now
                        if (taps >= DEVELOPER_TAPS) {
                            taps = 0
                            onOpenDeveloper()
                        }
                    }
                },
        )
    }
}
