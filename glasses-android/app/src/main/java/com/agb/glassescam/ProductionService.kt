package com.agb.glassescam

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.util.Log

/**
 * Keeps the app alive while production mode runs. It does no work itself: a foreground service (with the
 * microphone and connected-device types) is what lets the glasses' stream, the backend socket and the
 * speech recognizer keep working with the phone locked or the app in the background. Android needs the
 * ongoing notification for that.
 *
 * Start it while the app is on screen and the microphone permission is granted ([start] never throws);
 * [stop] ends it.
 */
class ProductionService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Glasses memory", NotificationManager.IMPORTANCE_LOW),
        )
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_headset)
            .setContentTitle("Glasses memory is running")
            .setContentText("Streaming what you see and listening when you press the button")
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        try {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
            )
        } catch (e: Exception) {
            // Missing microphone or Bluetooth permission, or started from the background.
            Log.w(TAG, "could not start in the foreground", e)
            stopSelf()
        }
        return START_NOT_STICKY
    }

    companion object {
        private const val TAG = "ProductionService"
        private const val CHANNEL = "production"
        private const val NOTIFICATION_ID = 1

        fun start(context: Context) {
            try {
                context.startForegroundService(Intent(context, ProductionService::class.java))
            } catch (e: Exception) {
                Log.w(TAG, "could not start the service", e)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, ProductionService::class.java))
        }
    }
}
