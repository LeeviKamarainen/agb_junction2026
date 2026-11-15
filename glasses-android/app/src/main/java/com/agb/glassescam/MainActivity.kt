package com.agb.glassescam

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
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
            if (granted) {
                viewModel.initialize(applicationContext)
                askProductionPermissions()
            }
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

    // Production mode starts by itself, so it asks for what it needs up front: the microphone (voice messages)
    // and, on Android 13+, notifications (the foreground service's). It runs either way; without the microphone
    // the glasses' button says so. Nothing is shown if both were granted before.
    private val productionPermissionsLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { startProduction() }

    private fun askProductionPermissions() {
        val wanted = buildList {
            add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
        }
        productionPermissionsLauncher.launch(wanted.toTypedArray())
    }

    private var supervising = false

    private fun startProduction() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED &&
            viewModel.ui.value.production
        ) {
            ProductionService.start(applicationContext)
        }
        if (supervising) return
        supervising = true
        // Every few seconds: open the glasses session if production should be running and is not (see
        // GlassesViewModel.superviseProduction). It outlives the screen going off, not the activity.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.CREATED) {
                while (true) {
                    viewModel.superviseProduction(::requestWearablesPermission)
                    delay(2_000)
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Production runs with the screen on, so a glance at the phone shows what the glasses are doing.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
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
