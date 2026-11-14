package com.agb.glassescam

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.material3.MaterialTheme
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlin.coroutines.resume

class MainActivity : ComponentActivity() {

    private val viewModel: GlassesViewModel by viewModels()

    // --- Meta AI app permission sheet (camera on the glasses) ---
    private val permissionMutex = Mutex()
    private var pendingPermission: CancellableContinuation<PermissionStatus>? = null

    private val wearablesPermissionLauncher =
        registerForActivityResult(Wearables.RequestPermissionContract()) { result ->
            pendingPermission?.resume(result.getOrDefault(PermissionStatus.Denied))
            pendingPermission = null
        }

    private suspend fun requestWearablesPermission(permission: Permission): PermissionStatus =
        permissionMutex.withLock {
            suspendCancellableCoroutine { cont ->
                pendingPermission = cont
                wearablesPermissionLauncher.launch(permission)
            }
        }

    // --- Android runtime permission for Bluetooth, required before Wearables.initialize ---
    private val bluetoothLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) viewModel.initialize(applicationContext)
        }

    // Phone CAMERA permission: only needed for the mock glasses' phone-camera feed
    private val phoneCameraLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) viewModel.enableMock(applicationContext)
        }

    // Microphone, for voice messages in memory mode: asked before memory mode is armed (not beside the
    // Meta AI app's camera sheet). Arming goes ahead either way; without it, holding the button says so.
    private val microphoneLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            viewModel.armMemory(::requestWearablesPermission)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        bluetoothLauncher.launch(Manifest.permission.BLUETOOTH_CONNECT)

        setContent {
            MaterialTheme {
                GlassesScreen(
                    viewModel = viewModel,
                    onRegister = { viewModel.register(this) },
                    onUnregister = { viewModel.unregister(this) },
                    onUpdateGlasses = { viewModel.openFirmwareUpdate(this) },
                    onToggleMock = { enabled ->
                        if (enabled) phoneCameraLauncher.launch(Manifest.permission.CAMERA)
                        else viewModel.disableMock()
                    },
                    onStart = { viewModel.startStreaming(::requestWearablesPermission) },
                    onArmQuick = { viewModel.armQuickShot(::requestWearablesPermission) },
                    onArmSnap = { viewModel.armSnapshot(::requestWearablesPermission) },
                    onArmLive = { viewModel.armLive(::requestWearablesPermission) },
                    onArmMemory = {
                        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                            viewModel.armMemory(::requestWearablesPermission)
                        } else {
                            microphoneLauncher.launch(Manifest.permission.RECORD_AUDIO)
                        }
                    },
                )
            }
        }
    }
}
