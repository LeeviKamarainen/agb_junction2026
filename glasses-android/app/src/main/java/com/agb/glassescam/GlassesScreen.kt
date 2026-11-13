package com.agb.glassescam

import androidx.compose.foundation.AndroidExternalSurface
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun GlassesScreen(
    viewModel: GlassesViewModel,
    onRegister: () -> Unit,
    onUnregister: () -> Unit,
    onToggleMock: (Boolean) -> Unit,
    onStart: () -> Unit,
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()

    Column(
        modifier = Modifier.statusBarsPadding().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("Registration: ${ui.registration}")
        Text("Devices: ${ui.deviceCount}   Session: ${ui.sessionState}   Stream: ${ui.streamState}")

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onRegister, enabled = ui.initialized) { Text("Register") }
            OutlinedButton(onClick = onUnregister, enabled = ui.initialized) { Text("Unregister") }
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
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onStart, enabled = ui.initialized) { Text("Start stream") }
            OutlinedButton(onClick = viewModel::stopStreaming) { Text("Stop") }
            Button(onClick = viewModel::capturePhoto) { Text("Capture") }
        }

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

        ui.lastPhoto?.let { photo ->
            Text("Last capture")
            Image(
                bitmap = photo.asImageBitmap(),
                contentDescription = "Last photo from glasses",
                modifier = Modifier.fillMaxWidth().aspectRatio(photo.width.toFloat() / photo.height),
            )
        }
    }
}
