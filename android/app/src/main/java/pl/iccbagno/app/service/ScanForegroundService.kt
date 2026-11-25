package pl.iccbagno.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import pl.iccbagno.app.MainActivity
import pl.iccbagno.app.data.ScanHistoryItem
import pl.iccbagno.app.data.ScanHistoryStore
import pl.iccbagno.app.data.StationsRepository
import pl.iccbagno.app.domain.FullTrainAvailabilityMap
import pl.iccbagno.app.domain.SeatFilterPrefs
import pl.iccbagno.app.optimizer.OptimizationProfile
import pl.iccbagno.app.optimizer.OptimizationStrategy
import pl.iccbagno.app.optimizer.OptimizerConfig
import pl.iccbagno.app.optimizer.SeatOptimizer
import pl.iccbagno.app.scraper.EbiletWebViewScanner
import pl.iccbagno.app.scraper.GrmParser
import pl.iccbagno.app.scraper.ScheduleClient

data class ScanProgressState(
    val isRunning: Boolean = false,
    val progress: Int = 0,
    val statusMessage: String = "",
    val logs: List<String> = emptyList(),
    val availabilityMap: FullTrainAvailabilityMap? = null,
    val strategies: List<OptimizationStrategy> = emptyList(),
    val error: String? = null,
    val ticketClass: Int = 2,
    val trainNumber: String = "",
    val fromStation: String = "",
    val toStation: String = "",
    val travelDate: String = "",
    val fastPathUsed: Boolean = false,
    val debugWebView: android.webkit.WebView? = null
)

class ScanForegroundService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var scanJob: Job? = null

    companion object {
        const val CHANNEL_ID = "iccbagno_scan_channel"
        const val CHANNEL_ID_DONE = "iccbagno_scan_done_channel"
        const val NOTIFICATION_ID = 1001
        private const val NOTIFICATION_ID_DONE = 2002
        const val ACTION_START = "pl.iccbagno.app.action.START_SCAN"
        const val ACTION_CANCEL = "pl.iccbagno.app.action.CANCEL_SCAN"

        const val EXTRA_FROM = "from_station"
        const val EXTRA_TO = "to_station"
        const val EXTRA_DATE = "travel_date"
        const val EXTRA_TRAIN = "train_number"
        const val EXTRA_DEP_TIME = "dep_time"
        const val EXTRA_CLASS = "ticket_class"
        const val EXTRA_PROFILE = "profile"
        const val EXTRA_FORCE_FULL = "force_full"
        const val EXTRA_ALLOW_BIKE = "allow_bike"
        const val EXTRA_ALLOW_PREGNANT = "allow_pregnant"
        const val EXTRA_ALLOW_CHILD = "allow_child"
        const val EXTRA_ALLOW_ELDERLY = "allow_elderly"
        const val EXTRA_ALLOW_PRM_ASSIST = "allow_prm_assist"
        const val EXTRA_ALLOW_WHEELCHAIR = "allow_wheelchair"

        private val _state = MutableStateFlow(ScanProgressState())
        val state: StateFlow<ScanProgressState> = _state.asStateFlow()

        @Volatile
        private var currentSeatFilters: SeatFilterPrefs = SeatFilterPrefs()

        fun startScan(
            context: Context,
            fromStation: String,
            toStation: String,
            travelDate: String,
            trainNumber: String,
            depTime: String,
            ticketClass: Int,
            profile: OptimizationProfile,
            forceFull: Boolean,
            seatFilters: SeatFilterPrefs = SeatFilterPrefs()
        ) {
            currentSeatFilters = seatFilters
            val intent = Intent(context, ScanForegroundService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_FROM, fromStation)
                putExtra(EXTRA_TO, toStation)
                putExtra(EXTRA_DATE, travelDate)
                putExtra(EXTRA_TRAIN, trainNumber)
                putExtra(EXTRA_DEP_TIME, depTime)
                putExtra(EXTRA_CLASS, ticketClass)
                putExtra(EXTRA_PROFILE, profile.name)
                putExtra(EXTRA_FORCE_FULL, forceFull)
                putExtra(EXTRA_ALLOW_BIKE, seatFilters.allowBike)
                putExtra(EXTRA_ALLOW_PREGNANT, seatFilters.allowPregnant)
                putExtra(EXTRA_ALLOW_CHILD, seatFilters.allowChild)
                putExtra(EXTRA_ALLOW_ELDERLY, seatFilters.allowElderly)
                putExtra(EXTRA_ALLOW_PRM_ASSIST, seatFilters.allowPrmAssist)
                putExtra(EXTRA_ALLOW_WHEELCHAIR, seatFilters.allowWheelchair)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun cancelScan(context: Context) {
            val intent = Intent(context, ScanForegroundService::class.java).apply {
                action = ACTION_CANCEL
            }
            context.startService(intent)
        }

        fun recomputeProfile(profile: OptimizationProfile) {
            recomputeStrategies(profile, currentSeatFilters)
        }

        fun recomputeStrategies(profile: OptimizationProfile, seatFilters: SeatFilterPrefs) {
            currentSeatFilters = seatFilters
            val current = _state.value
            val map = current.availabilityMap ?: return
            val strategies = computeStrategiesForMap(map, profile, seatFilters)
            _state.value = current.copy(
                strategies = strategies,
                error = if (strategies.isEmpty()) buildFilteredEmptyMessage(map, current.ticketClass, seatFilters) else null
            )
        }

        fun loadFromHistory(
            item: ScanHistoryItem,
            profile: OptimizationProfile,
            seatFilters: SeatFilterPrefs = currentSeatFilters
        ) {
            currentSeatFilters = seatFilters
            val strategies = computeStrategiesForMap(item.availabilityMap, profile, seatFilters)
            _state.value = ScanProgressState(
                isRunning = false,
                progress = 100,
                statusMessage = "Wczytano z historii analiz",
                logs = emptyList(),
                availabilityMap = item.availabilityMap,
                strategies = strategies,
                error = if (strategies.isEmpty()) buildFilteredEmptyMessage(item.availabilityMap, item.ticketClass, seatFilters) else null,
                ticketClass = item.ticketClass,
                trainNumber = item.trainNumber,
                fromStation = item.fromStation,
                toStation = item.toStation,
                travelDate = item.travelDate,
                fastPathUsed = item.availabilityMap.fastPathUsed
            )
        }

        private fun buildFilteredEmptyMessage(
            map: FullTrainAvailabilityMap,
            ticketClass: Int,
            seatFilters: SeatFilterPrefs
        ): String {
            val anyRawSeats = map.segments.any { seg -> seg.carriages.values.any { it.isNotEmpty() } }
            val anyAllowedSeats = map.segments.any { seg ->
                seg.carriages.any { (wNo, seats) ->
                    seats.any { sNo ->
                        val tags = seg.seatTags["${wNo}_$sNo"].orEmpty()
                        GrmParser.isSeatAllowed(tags, seatFilters)
                    }
                }
            }
            return when {
                anyRawSeats && !anyAllowedSeats ->
                    "Wolne miejsca w Klasie $ticketClass są dostępne tylko w wyłączonych kategoriach specjalnych (np. bez roweru lub wymagające zaznaczenia w filtrach)."
                anyRawSeats ->
                    "Brak opłacalnych wariantów siedzących spełniających wybrane filtry w Klasie $ticketClass."
                else -> "Brak wariantów siedzących w zapisanej analizie."
            }
        }

        private fun computeStrategiesForMap(
            map: FullTrainAvailabilityMap,
            profile: OptimizationProfile,
            seatFilters: SeatFilterPrefs = currentSeatFilters
        ): List<OptimizationStrategy> {
            val matrix = SeatOptimizer.availabilityMapToMatrix(map)
            if (matrix.availabilityMatrix.isEmpty()) return emptyList()
            val evas = HashMap<String, String>()
            val depTimes = ArrayList<String>(map.segments.size)
            val segDates = ArrayList<String>(map.segments.size)
            for (seg in map.segments) {
                if (seg.fromEva.isNotBlank()) evas[seg.fromStation] = seg.fromEva
                if (seg.toEva.isNotBlank()) evas[seg.toStation] = seg.toEva
                depTimes.add(seg.departureTime)
                segDates.add(seg.segmentDate.ifBlank { map.travelDate })
            }
            return SeatOptimizer.solveTopStrategies(
                availabilityMatrix = matrix.availabilityMatrix,
                seatsMeta = matrix.seatsMeta,
                stationNames = matrix.stationNames,
                segmentDurations = matrix.segmentDurations,
                config = OptimizerConfig(profile = profile, seatFilters = seatFilters),
                stationEvas = evas,
                departureTimes = depTimes,
                segmentDates = segDates,
                defaultTravelDate = map.travelDate
            )
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CANCEL -> {
                scanJob?.cancel()
                _state.value = _state.value.copy(
                    isRunning = false,
                    statusMessage = "Skanowanie anulowane przez użytkownika.",
                    debugWebView = null
                )
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
            ACTION_START -> {
                val fromSt = intent.getStringExtra(EXTRA_FROM) ?: return START_NOT_STICKY
                val toSt = intent.getStringExtra(EXTRA_TO) ?: return START_NOT_STICKY
                val date = intent.getStringExtra(EXTRA_DATE) ?: return START_NOT_STICKY
                val train = intent.getStringExtra(EXTRA_TRAIN) ?: return START_NOT_STICKY
                val depTime = intent.getStringExtra(EXTRA_DEP_TIME) ?: "12:00"
                val ticketClass = intent.getIntExtra(EXTRA_CLASS, 2)
                val profile = SeatOptimizer.parseProfile(intent.getStringExtra(EXTRA_PROFILE))
                val forceFull = intent.getBooleanExtra(EXTRA_FORCE_FULL, false)
                val seatFilters = SeatFilterPrefs(
                    allowBike = intent.getBooleanExtra(EXTRA_ALLOW_BIKE, true),
                    allowPregnant = intent.getBooleanExtra(EXTRA_ALLOW_PREGNANT, false),
                    allowChild = intent.getBooleanExtra(EXTRA_ALLOW_CHILD, false),
                    allowElderly = intent.getBooleanExtra(EXTRA_ALLOW_ELDERLY, false),
                    allowPrmAssist = intent.getBooleanExtra(EXTRA_ALLOW_PRM_ASSIST, false),
                    allowWheelchair = intent.getBooleanExtra(EXTRA_ALLOW_WHEELCHAIR, false)
                )
                currentSeatFilters = seatFilters

                val notif = buildNotification(train, 0, "Uruchamianie skanera...")
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    startForeground(NOTIFICATION_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
                } else {
                    startForeground(NOTIFICATION_ID, notif)
                }

                runScan(fromSt, toSt, date, train, depTime, ticketClass, profile, forceFull, seatFilters)
            }
        }
        return START_NOT_STICKY
    }

    private fun runScan(
        fromStation: String,
        toStation: String,
        travelDate: String,
        trainNumber: String,
        depTime: String,
        ticketClass: Int,
        profile: OptimizationProfile,
        forceFull: Boolean,
        seatFilters: SeatFilterPrefs
    ) {
        scanJob?.cancel()
        _state.value = ScanProgressState(
            isRunning = true,
            progress = 2,
            statusMessage = "Przygotowywanie skanera...",
            logs = listOf("Start skanowania $trainNumber ($fromStation -> $toStation)"),
            ticketClass = ticketClass,
            trainNumber = trainNumber,
            fromStation = fromStation,
            toStation = toStation,
            travelDate = travelDate
        )

        scanJob = serviceScope.launch {
            val repo = StationsRepository(applicationContext)
            val scheduleClient = ScheduleClient(repo)
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            try {
                nm.cancel(NOTIFICATION_ID_DONE)
            } catch (_: Exception) {
            }

            val scanner = EbiletWebViewScanner(
                context = applicationContext,
                scheduleClient = scheduleClient,
                onProgress = { pct, msg ->
                    val cur = _state.value
                    val nextPct = maxOf(cur.progress, pct)
                    val updatedLogs = (cur.logs + "[$nextPct%] $msg").takeLast(40)
                    _state.value = cur.copy(progress = nextPct, statusMessage = msg, logs = updatedLogs)
                    nm.notify(NOTIFICATION_ID, buildNotification(trainNumber, nextPct, msg))
                },
                onAttachDebugWebView = { wv ->
                    _state.value = _state.value.copy(debugWebView = wv)
                }
            )

            try {
                val map = scanner.scanFullTrain(
                    fromStation = fromStation,
                    toStation = toStation,
                    travelDate = travelDate,
                    trainNumber = trainNumber,
                    departureTime = depTime,
                    ticketClass = ticketClass,
                    fastPath = !forceFull,
                    seatFilters = seatFilters
                )

                val matrix = SeatOptimizer.availabilityMapToMatrix(map)
                if (matrix.availabilityMatrix.isEmpty()) {
                    val scrapeErrorSegs = map.segments.filter { it.status == "SCRAPE_ERROR" }
                    val errorText = if (scrapeErrorSegs.isNotEmpty()) {
                        val sampleReason = scrapeErrorSegs
                            .mapNotNull { it.errorReason?.trim()?.removePrefix("SCRAPE_ERROR:")?.trim() }
                            .firstOrNull { it.isNotBlank() }
                            ?: "Nie udało się odczytać schematu miejsc z systemu e-IC."
                        "Błąd pobierania schematu (${scrapeErrorSegs.size}/${map.segments.size} odcinków): $sampleReason"
                    } else {
                        "Brak wolnych miejsc w Klasie $ticketClass dla pociągu $trainNumber."
                    }
                    _state.value = _state.value.copy(
                        isRunning = false,
                        error = errorText,
                        debugWebView = null
                    )
                    notifyScanCompleted(nm, trainNumber, errorText)
                    return@launch
                }

                val strategies = computeStrategiesForMap(map, profile, seatFilters)
                if (strategies.isEmpty()) {
                    val anyAllowedRaw = map.segments.any { seg ->
                        seg.carriages.any { (wNo, seats) ->
                            seats.any { sNo -> GrmParser.isSeatAllowed(seg.seatTags["${wNo}_$sNo"].orEmpty(), seatFilters) }
                        }
                    }
                    val freeSegments = map.segments.filter { seg -> seg.carriages.values.any { it.isNotEmpty() } }
                    val errSegs = map.segments.filter { it.status == "SCRAPE_ERROR" }
                    val details = when {
                        freeSegments.isNotEmpty() && !anyAllowedRaw ->
                            "Znaleziono wolne miejsca w Klasie $ticketClass, ale wszystkie wymagają włączenia odpowiedniej kategorii w filtrach."
                        freeSegments.isNotEmpty() -> {
                            val listStr = freeSegments.take(3).joinToString("; ") { seg ->
                                "${seg.fromStation} -> ${seg.toStation}"
                            }
                            "Większość trasy jest wyprzedana - znaleziono tylko pojedyncze krótkie odcinki ($listStr), na których nie opłaca się przesiadka w optymalizatorze."
                        }
                        errSegs.isNotEmpty() -> {
                            val reason = errSegs.mapNotNull { it.errorReason }.firstOrNull() ?: "Błąd odczytu."
                            "Błąd odczytu (${errSegs.size}/${map.segments.size} odcinków): $reason"
                        }
                        else -> "Brak wariantów podróży z miejscem siedzącym w Klasie $ticketClass."
                    }
                    _state.value = _state.value.copy(
                        isRunning = false,
                        progress = 100,
                        statusMessage = details,
                        availabilityMap = map,
                        strategies = emptyList(),
                        error = details,
                        debugWebView = null
                    )
                    notifyScanCompleted(nm, trainNumber, details)
                    return@launch
                }

                ScanHistoryStore.saveScan(
                    context = applicationContext,
                    fromStation = fromStation,
                    toStation = toStation,
                    travelDate = travelDate,
                    trainNumber = trainNumber,
                    ticketClass = ticketClass,
                    map = map,
                    strategies = strategies
                )

                _state.value = _state.value.copy(
                    isRunning = false,
                    progress = 100,
                    statusMessage = "Zakończono pomyślnie!",
                    availabilityMap = map,
                    strategies = strategies,
                    fastPathUsed = map.fastPathUsed,
                    error = null,
                    debugWebView = null
                )
                notifyScanCompleted(
                    nm,
                    trainNumber,
                    "Znaleziono: ${strategies.first().title} ($fromStation -> $toStation)"
                )
            } catch (_: CancellationException) {
                _state.value = _state.value.copy(
                    isRunning = false,
                    statusMessage = "Skanowanie anulowane.",
                    debugWebView = null
                )
            } catch (e: Exception) {
                val errMsg = e.message ?: "Błąd podczas skanowania."
                _state.value = _state.value.copy(
                    isRunning = false,
                    error = errMsg,
                    debugWebView = null
                )
                notifyScanCompleted(nm, trainNumber, errMsg)
            } finally {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val progressChannel = NotificationChannel(
                CHANNEL_ID,
                "Skanowanie pociągów",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Postęp wyszukiwania wolnych miejsc PKP"
            }
            val doneChannel = NotificationChannel(
                CHANNEL_ID_DONE,
                "Wyniki skanowania",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Powiadomienia o zakończeniu wyszukiwania miejsc"
                enableVibration(true)
            }
            nm.createNotificationChannel(progressChannel)
            nm.createNotificationChannel(doneChannel)
        }
    }

    private fun buildNotification(trainNumber: String, progress: Int, text: String): Notification {
        val openIntent = Intent(this, MainActivity::class.java)
        val openPending = PendingIntent.getActivity(
            this, 0, openIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val cancelIntent = Intent(this, ScanForegroundService::class.java).apply {
            action = ACTION_CANCEL
        }
        val cancelPending = PendingIntent.getService(
            this, 1, cancelIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("SiadaICie: skanowanie $trainNumber ($progress%)")
            .setContentText(text)
            .setProgress(100, progress, false)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(openPending)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Anuluj", cancelPending)
            .build()
    }

    private fun notifyScanCompleted(nm: NotificationManager, trainNumber: String, summary: String) {
        try {
            val openIntent = Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val openPending = PendingIntent.getActivity(
                this, 2, openIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val notification = NotificationCompat.Builder(this, CHANNEL_ID_DONE)
                .setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle("Zakończono skanowanie ($trainNumber)")
                .setContentText(summary)
                .setStyle(NotificationCompat.BigTextStyle().bigText(summary))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setDefaults(NotificationCompat.DEFAULT_ALL)
                .setCategory(NotificationCompat.CATEGORY_STATUS)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setAutoCancel(true)
                .setContentIntent(openPending)
                .build()
            nm.notify(NOTIFICATION_ID_DONE, notification)
        } catch (_: Exception) {
        }
    }

    override fun onDestroy() {
        serviceScope.cancel()
        super.onDestroy()
    }
}
