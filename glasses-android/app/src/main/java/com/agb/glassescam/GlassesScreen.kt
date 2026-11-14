package com.agb.glassescam

import androidx.compose.foundation.AndroidExternalSurface
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.background
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import kotlinx.coroutines.delay
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/** Big, obvious status for the capture pipeline: capturing -> receiving (with %) -> saving -> sending -> done. */
@Composable
private fun CaptureStatusCard(ui: UiState) {
    if (ui.captureStage == CaptureStage.IDLE) return

    // Seconds spent in the current stage; restarts whenever the stage changes.
    var elapsed by remember(ui.captureStage) { mutableIntStateOf(0) }
    LaunchedEffect(ui.captureStage) {
        while (true) {
            delay(1000)
            elapsed++
        }
    }

    val (title, color) = when (ui.captureStage) {
        CaptureStage.CAPTURING -> "1/4  Capturing on the glasses..." to Color(0xFFFFF3CD)
        CaptureStage.TRANSFERRING ->
            "2/4  Receiving photo ${(ui.captureProgress * 100).toInt()}%" to Color(0xFFD6E9FF)
        CaptureStage.SAVING -> "3/4  Saving..." to Color(0xFFD6E9FF)
        CaptureStage.SENDING -> "4/4  Sending to backend..." to Color(0xFFD6E9FF)
        CaptureStage.DONE -> "Done. Photo saved" to Color(0xFFD4EDDA)
        CaptureStage.FAILED -> "Capture failed" to Color(0xFFF8D7DA)
        CaptureStage.IDLE -> "" to Color.Transparent
    }

    Column(
        modifier = Modifier.fillMaxWidth().background(color).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(title)
        when (ui.captureStage) {
            // Known size: real progress bar (only changes when bytes arrive, so no constant redraws)
            CaptureStage.TRANSFERRING -> LinearProgressIndicator(
                progress = { ui.captureProgress },
                modifier = Modifier.fillMaxWidth(),
            )
            // Working, progress unknown: show elapsed seconds instead of an endless animation
            // (an infinite animation redraws every frame and floods the log).
            CaptureStage.CAPTURING, CaptureStage.SAVING, CaptureStage.SENDING -> {
                Text(
                    if (ui.captureStage == CaptureStage.CAPTURING && elapsed >= 5) {
                        "Waiting for the glasses to start sending... ${elapsed}s. " +
                            "Older photos on the glasses may be sent first."
                    } else {
                        "${elapsed}s"
                    },
                )
            }
            else -> Unit
        }
        if (ui.captureDetail.isNotBlank()) Text(ui.captureDetail)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun GlassesScreen(
    viewModel: GlassesViewModel,
    onRegister: () -> Unit,
    onUnregister: () -> Unit,
    onUpdateGlasses: () -> Unit,
    onToggleMock: (Boolean) -> Unit,
    onStart: () -> Unit,
    onArmQuick: () -> Unit,
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var showGallery by rememberSaveable { mutableStateOf(false) }

    // The live-preview screen stays composed underneath the gallery so the video surface and
    // decoder keep running; the gallery is drawn over it.
    Box(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.statusBarsPadding().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("Registration: ${ui.registration}")
            Text("Devices: ${ui.deviceCount}   Session: ${ui.sessionState}   Stream: ${ui.streamState}")

            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Button(onClick = onRegister, enabled = ui.initialized) { Text("Register") }
                OutlinedButton(onClick = onUnregister, enabled = ui.initialized) { Text("Unregister") }
                OutlinedButton(onClick = onUpdateGlasses, enabled = ui.initialized) { Text("Update glasses app") }
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Switch(
                    checked = ui.mockEnabled,
                    onCheckedChange = onToggleMock,
                    enabled = ui.initialized,
                )
                Text("Mock glasses (phone camera)")
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Switch(
                    checked = ui.useDisplay,
                    onCheckedChange = viewModel::setUseDisplay,
                    enabled = ui.initialized && !ui.quickArmed,
                )
                Text("Glasses screen messages (may block photo transfer)")
            }
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Button(onClick = onStart, enabled = ui.initialized && !ui.quickArmed) { Text("Start stream") }
                // Quick shot: no video stream. Press the glasses capture button / Neural Band to take a photo.
                Button(
                    onClick = { if (ui.quickArmed) viewModel.stopStreaming() else onArmQuick() },
                    enabled = ui.initialized,
                ) { Text(if (ui.quickArmed) "Disarm quick shot" else "Arm quick shot") }
                OutlinedButton(onClick = viewModel::stopStreaming) { Text("Stop") }
                Button(onClick = viewModel::capturePhoto) { Text("Capture") }
                OutlinedButton(onClick = viewModel::cycleProfile) { Text("Photo: ${ui.photoProfile}") }
                OutlinedButton(onClick = { showGallery = true }) { Text("Gallery (${ui.photos.size})") }
            }

            CaptureStatusCard(ui)

            if (ui.message.isNotBlank()) Text(ui.message)

            // Live preview: HevcDecoder renders decoded frames straight into this surface.
            AndroidExternalSurface(
                modifier = Modifier.fillMaxWidth().height(420.dp),
            ) {
                onSurface { surface, _, _ ->
                    viewModel.setSurface(surface)
                    surface.onDestroyed { viewModel.setSurface(null) }
                }
            }
        }

        if (showGallery) {
            GalleryScreen(
                photos = ui.photos,
                onClose = { showGallery = false },
                onDelete = viewModel::deletePhoto,
            )
        }
    }
}
