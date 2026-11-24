package pl.iccbagno.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import pl.iccbagno.app.optimizer.OptimizationProfile
import pl.iccbagno.app.service.ScanForegroundService
import pl.iccbagno.app.ui.IccBagnoTheme
import pl.iccbagno.app.ui.MainScreen
import pl.iccbagno.app.ui.MainViewModel

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    private val requestNotifPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { _ -> }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                requestNotifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }

        handleDebugScanIntent(intent)

        setContent {
            val ui by viewModel.uiState.collectAsState()
            IccBagnoTheme(themeMode = ui.themeMode) {
                MainScreen(viewModel = viewModel)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleDebugScanIntent(intent)
    }

    private fun handleDebugScanIntent(intent: Intent?) {
        if (!BuildConfig.DEBUG || intent == null) return
        val from = intent.getStringExtra("debug_from")?.takeIf { it.isNotBlank() } ?: return
        val to = intent.getStringExtra("debug_to")?.takeIf { it.isNotBlank() } ?: return
        val date = intent.getStringExtra("debug_date")?.takeIf { it.isNotBlank() } ?: return
        val train = intent.getStringExtra("debug_train")?.takeIf { it.isNotBlank() } ?: return
        val dep = intent.getStringExtra("debug_dep") ?: "12:00"
        val cls = intent.getIntExtra("debug_class", 2)
        val forceFull = intent.getBooleanExtra("debug_force_full", false)
        ScanForegroundService.startScan(
            context = applicationContext,
            fromStation = from,
            toStation = to,
            travelDate = date,
            trainNumber = train,
            depTime = dep,
            ticketClass = cls,
            profile = OptimizationProfile.BALANCED,
            forceFull = forceFull
        )
    }
}
