package pl.iccbagno.app.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.view.ViewGroup
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SwapVert
import androidx.compose.material.icons.filled.Train
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import pl.iccbagno.app.domain.StationInfo
import pl.iccbagno.app.domain.TrainConnection
import pl.iccbagno.app.optimizer.OptimizationProfile
import pl.iccbagno.app.optimizer.OptimizationStrategy
import pl.iccbagno.app.optimizer.PathStep
import pl.iccbagno.app.optimizer.SeatOptimizer
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun MainScreen(viewModel: MainViewModel) {
    val ui by viewModel.uiState.collectAsState()
    val scan by viewModel.scanState.collectAsState()
    val context = LocalContext.current

    var showSettings by remember { mutableStateOf(false) }
    var showDatePicker by remember { mutableStateOf(false) }
    var showTimePicker by remember { mutableStateOf(false) }
    var showLogs by remember { mutableStateOf(false) }

    Scaffold(
        containerColor = PkpDarkBg,
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = Icons.Default.Train,
                            contentDescription = null,
                            tint = PkpOrange,
                            modifier = Modifier.size(24.dp)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Column {
                            Text("IccBagno Mobile", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                            Text("Lokalny optymalizator miejsc PKP", fontSize = 11.sp, color = PkpTextSecondary)
                        }
                    }
                },
                actions = {
                    IconButton(onClick = { showSettings = true }) {
                        Icon(Icons.Default.Settings, contentDescription = "Ustawienia", tint = PkpTextPrimary)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = PkpSurface,
                    titleContentColor = PkpTextPrimary
                )
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            // 1. Search Form Card
            SearchCard(
                ui = ui,
                onFromChange = viewModel::onFromQueryChange,
                onToChange = viewModel::onToQueryChange,
                onSelectFrom = viewModel::selectFromStation,
                onSelectTo = viewModel::selectToStation,
                onSwap = viewModel::swapStations,
                onOpenDate = { showDatePicker = true },
                onOpenTime = { showTimePicker = true },
                onResetTime = { viewModel.setMinDepartureTime("00:00") },
                onSelectClass = viewModel::setTicketClass,
                onSearchTrains = viewModel::searchTrains
            )

            // 2. Scan Progress Card (when scanning or error)
            if (scan.isRunning || scan.error != null) {
                ScanProgressCard(
                    trainNumber = scan.trainNumber,
                    isRunning = scan.isRunning,
                    progress = scan.progress,
                    message = scan.statusMessage,
                    error = scan.error,
                    logs = scan.logs,
                    showLogs = showLogs,
                    onToggleLogs = { showLogs = !showLogs },
                    onCancel = viewModel::cancelScan,
                    debugWebView = if (ui.debugWebViewEnabled) scan.debugWebView else null
                )
            }

            // 3. Optimization Results (when available)
            if (!scan.isRunning && scan.strategies.isNotEmpty()) {
                val safeIdx = ui.selectedStrategyIdx.coerceIn(0, scan.strategies.lastIndex)
                val selectedStrategy = scan.strategies[safeIdx]

                ResultsSection(
                    trainNumber = scan.trainNumber,
                    fastPathUsed = scan.fastPathUsed,
                    profile = ui.profile,
                    onProfileChange = viewModel::setProfile,
                    strategies = scan.strategies,
                    selectedIdx = safeIdx,
                    onSelectStrategy = viewModel::selectStrategy,
                    selectedStrategy = selectedStrategy,
                    onOpenBooking = { url -> openUrlSafe(context, url) }
                )
            }

            // 4. Train List Card
            if (ui.trainsError != null) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = PkpSurface),
                    border = BorderStroke(1.dp, PkpRed.copy(alpha = 0.5f))
                ) {
                    Text(
                        text = ui.trainsError.orEmpty(),
                        color = PkpRed,
                        modifier = Modifier.padding(14.dp),
                        fontSize = 13.sp
                    )
                }
            }

            if (ui.trains.isNotEmpty()) {
                val visibleTrains = ui.trains.filter { train ->
                    train.departure.trim().padStart(5, '0') >= ui.minDepartureTime
                }
                val headerLabel = if (ui.minDepartureTime != "00:00") {
                    "Pociągi PKP Intercity (widoczne ${visibleTrains.size} z ${ui.trains.size}, od ${ui.minDepartureTime})"
                } else {
                    "Pociągi PKP Intercity (${ui.trains.size})"
                }
                Text(
                    text = headerLabel,
                    fontWeight = FontWeight.Bold,
                    color = PkpTextPrimary,
                    fontSize = 15.sp
                )
                if (visibleTrains.isEmpty()) {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = PkpSurface),
                        border = BorderStroke(1.dp, PkpBorder)
                    ) {
                        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(
                                text = "Brak pociągów po godzinie ${ui.minDepartureTime}.",
                                color = PkpTextSecondary,
                                fontSize = 13.sp
                            )
                            OutlinedButton(
                                onClick = { viewModel.setMinDepartureTime("00:00") },
                                border = BorderStroke(1.dp, PkpOrange)
                            ) {
                                Text("Pokaż cały dzień (od 00:00)", color = PkpOrange, fontSize = 12.sp)
                            }
                        }
                    }
                } else {
                    for (train in visibleTrains) {
                        TrainConnectionCard(
                            train = train,
                            enabled = !scan.isRunning,
                            onScanClick = { viewModel.startTrainScan(train) }
                        )
                    }
                }
            }
        }
    }

    if (showDatePicker) {
        val initialMillis = remember(ui.travelDate) {
            try {
                LocalDate.parse(ui.travelDate).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
            } catch (_: Exception) {
                System.currentTimeMillis()
            }
        }
        val pickerState = rememberDatePickerState(initialSelectedDateMillis = initialMillis)
        DatePickerDialog(
            onDismissRequest = { showDatePicker = false },
            confirmButton = {
                TextButton(onClick = {
                    val millis = pickerState.selectedDateMillis
                    if (millis != null) {
                        val date = Instant.ofEpochMilli(millis).atZone(ZoneOffset.UTC).toLocalDate()
                        viewModel.setTravelDate(date.format(DateTimeFormatter.ISO_LOCAL_DATE))
                    }
                    showDatePicker = false
                }) { Text("Wybierz", color = PkpOrange) }
            },
            dismissButton = {
                TextButton(onClick = { showDatePicker = false }) { Text("Anuluj") }
            }
        ) {
            DatePicker(state = pickerState)
        }
    }

    if (showTimePicker) {
        val parts = ui.minDepartureTime.split(":")
        val initH = parts.getOrNull(0)?.toIntOrNull()?.coerceIn(0, 23) ?: 0
        val initM = parts.getOrNull(1)?.toIntOrNull()?.coerceIn(0, 59) ?: 0
        val timeState = rememberTimePickerState(initialHour = initH, initialMinute = initM, is24Hour = true)
        AlertDialog(
            onDismissRequest = { showTimePicker = false },
            containerColor = PkpSurface,
            title = { Text("Od godziny", color = PkpTextPrimary, fontWeight = FontWeight.Bold) },
            text = { TimePicker(state = timeState) },
            confirmButton = {
                TextButton(onClick = {
                    val formatted = "%02d:%02d".format(timeState.hour, timeState.minute)
                    viewModel.setMinDepartureTime(formatted)
                    showTimePicker = false
                }) { Text("Wybierz", color = PkpOrange) }
            },
            dismissButton = {
                TextButton(onClick = { showTimePicker = false }) { Text("Anuluj") }
            }
        )
    }

    if (showSettings) {
        AlertDialog(
            onDismissRequest = { showSettings = false },
            containerColor = PkpSurface,
            title = { Text("Ustawienia skanera", color = PkpTextPrimary, fontWeight = FontWeight.Bold) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("Tylko pociągi bezpośrednie", color = PkpTextPrimary, fontSize = 14.sp)
                            Text("Ukrywa połączenia z przesiadkami z rozkładu", color = PkpTextSecondary, fontSize = 11.sp)
                        }
                        Switch(checked = ui.directOnly, onCheckedChange = viewModel::setDirectOnly)
                    }
                    HorizontalDivider(color = PkpBorder)
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("Pomiń Fast-Path", color = PkpTextPrimary, fontSize = 14.sp)
                            Text("Zawsze skanuj każdy odcinek po kolei", color = PkpTextSecondary, fontSize = 11.sp)
                        }
                        Switch(checked = ui.forceFullScan, onCheckedChange = viewModel::setForceFullScan)
                    }
                    HorizontalDivider(color = PkpBorder)
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("Podgląd WebView (diagnostyka)", color = PkpTextPrimary, fontSize = 14.sp)
                            Text("Pokazuje ukryty widok ebilet.intercity.pl w trakcie skanu", color = PkpTextSecondary, fontSize = 11.sp)
                        }
                        Switch(checked = ui.debugWebViewEnabled, onCheckedChange = viewModel::setDebugWebViewEnabled)
                    }
                    HorizontalDivider(color = PkpBorder)
                    Text(
                        "Bezpieczeństwo: połączenia wyłącznie HTTPS, brak zbierania danych, cookies i cache WebView czyszczone po każdym skanie. Płatność odbywa się w zewnętrznej przeglądarce e-IC.",
                        fontSize = 11.sp,
                        color = PkpTextSecondary
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = { showSettings = false }) { Text("Zamknij", color = PkpOrange) }
            }
        )
    }
}

@Composable
private fun SearchCard(
    ui: SearchUiState,
    onFromChange: (String) -> Unit,
    onToChange: (String) -> Unit,
    onSelectFrom: (StationInfo) -> Unit,
    onSelectTo: (StationInfo) -> Unit,
    onSwap: () -> Unit,
    onOpenDate: () -> Unit,
    onOpenTime: () -> Unit,
    onResetTime: () -> Unit,
    onSelectClass: (Int) -> Unit,
    onSearchTrains: () -> Unit
) {
    val fieldColors = OutlinedTextFieldDefaults.colors(
        focusedBorderColor = PkpOrange,
        unfocusedBorderColor = PkpBorder,
        focusedTextColor = PkpTextPrimary,
        unfocusedTextColor = PkpTextPrimary,
        focusedLabelColor = PkpOrange,
        unfocusedLabelColor = PkpTextSecondary
    )

    Card(
        colors = CardDefaults.cardColors(containerColor = PkpSurface),
        border = BorderStroke(1.dp, PkpBorder),
        shape = RoundedCornerShape(14.dp)
    ) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = ui.fromQuery,
                        onValueChange = onFromChange,
                        label = { Text("Stacja początkowa") },
                        singleLine = true,
                        colors = fieldColors,
                        modifier = Modifier.fillMaxWidth()
                    )
                    if (ui.fromSuggestions.isNotEmpty()) {
                        SuggestionsBox(ui.fromSuggestions, onSelectFrom)
                    }

                    OutlinedTextField(
                        value = ui.toQuery,
                        onValueChange = onToChange,
                        label = { Text("Stacja docelowa") },
                        singleLine = true,
                        colors = fieldColors,
                        modifier = Modifier.fillMaxWidth()
                    )
                    if (ui.toSuggestions.isNotEmpty()) {
                        SuggestionsBox(ui.toSuggestions, onSelectTo)
                    }
                }
                Spacer(modifier = Modifier.width(6.dp))
                IconButton(
                    onClick = onSwap,
                    modifier = Modifier
                        .clip(RoundedCornerShape(10.dp))
                        .background(PkpSurfaceAlt)
                ) {
                    Icon(Icons.Default.SwapVert, contentDescription = "Zamień stacje", tint = PkpOrange)
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedButton(
                    onClick = onOpenDate,
                    border = BorderStroke(1.dp, PkpBorder),
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.CalendarMonth, contentDescription = null, tint = PkpOrange, modifier = Modifier.size(16.dp))
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(ui.travelDate, color = PkpTextPrimary, fontSize = 13.sp)
                }

                OutlinedButton(
                    onClick = onOpenTime,
                    border = BorderStroke(1.dp, if (ui.minDepartureTime != "00:00") PkpOrange else PkpBorder),
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.Schedule, contentDescription = null, tint = PkpOrange, modifier = Modifier.size(16.dp))
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("Od ${ui.minDepartureTime}", color = PkpTextPrimary, fontSize = 13.sp)
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf(2 to "Kl. 2", 1 to "Kl. 1").forEach { (cls, label) ->
                        FilterChip(
                            selected = ui.ticketClass == cls,
                            onClick = { onSelectClass(cls) },
                            label = { Text(label, fontWeight = FontWeight.SemiBold) },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = PkpOrange,
                                selectedLabelColor = Color.White,
                                containerColor = PkpSurfaceAlt,
                                labelColor = PkpTextSecondary
                            )
                        )
                    }
                }
                if (ui.minDepartureTime != "00:00") {
                    TextButton(onClick = onResetTime) {
                        Text("Cały dzień", color = PkpOrange, fontSize = 12.sp)
                    }
                }
            }

            Button(
                onClick = onSearchTrains,
                enabled = !ui.isLoadingTrains,
                colors = ButtonDefaults.buttonColors(containerColor = PkpOrange),
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
            ) {
                if (ui.isLoadingTrains) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), color = Color.White, strokeWidth = 2.dp)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("Szukanie pociągów...", fontWeight = FontWeight.Bold)
                } else {
                    Icon(Icons.Default.Search, contentDescription = null)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("Szukaj pociągów", fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

@Composable
private fun SuggestionsBox(items: List<StationInfo>, onSelect: (StationInfo) -> Unit) {
    Surface(
        color = PkpSurfaceAlt,
        border = BorderStroke(1.dp, PkpBorder),
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(max = 180.dp)
    ) {
        Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
            for (st in items) {
                Text(
                    text = st.name,
                    color = PkpTextPrimary,
                    fontSize = 14.sp,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { onSelect(st) }
                        .padding(horizontal = 12.dp, vertical = 9.dp)
                )
                HorizontalDivider(color = PkpBorder.copy(alpha = 0.5f))
            }
        }
    }
}

@Composable
private fun ScanProgressCard(
    trainNumber: String,
    isRunning: Boolean,
    progress: Int,
    message: String,
    error: String?,
    logs: List<String>,
    showLogs: Boolean,
    onToggleLogs: () -> Unit,
    onCancel: () -> Unit,
    debugWebView: android.webkit.WebView?
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = PkpSurface),
        border = BorderStroke(1.dp, if (error != null) PkpRed else PkpOrange.copy(alpha = 0.6f)),
        shape = RoundedCornerShape(14.dp)
    ) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = if (isRunning) "Skanowanie $trainNumber ($progress%)" else "Błąd skanowania $trainNumber",
                    fontWeight = FontWeight.Bold,
                    color = if (error != null) PkpRed else PkpOrange,
                    fontSize = 15.sp
                )
                if (isRunning) {
                    TextButton(onClick = onCancel) {
                        Icon(Icons.Default.Close, contentDescription = null, tint = PkpRed, modifier = Modifier.size(18.dp))
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("Anuluj", color = PkpRed)
                    }
                }
            }

            if (isRunning) {
                LinearProgressIndicator(
                    progress = { (progress / 100f).coerceIn(0f, 1f) },
                    color = PkpOrange,
                    trackColor = PkpSurfaceAlt,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(8.dp)
                        .clip(RoundedCornerShape(4.dp))
                )
            }

            Text(
                text = error ?: message,
                color = if (error != null) PkpRed else PkpTextPrimary,
                fontSize = 13.sp
            )

            TextButton(onClick = onToggleLogs) {
                Text(
                    if (showLogs) "Ukryj logi (${logs.size})" else "Pokaż logi (${logs.size})",
                    color = PkpTextSecondary,
                    fontSize = 12.sp
                )
            }

            if (showLogs) {
                Surface(
                    color = PkpDarkBg,
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(max = 160.dp)
                ) {
                    Column(
                        modifier = Modifier
                            .verticalScroll(rememberScrollState())
                            .padding(8.dp)
                    ) {
                        for (line in logs) {
                            Text(line, color = PkpTextSecondary, fontSize = 11.sp)
                        }
                    }
                }
            }

            if (debugWebView != null && isRunning) {
                Text("Podgląd diagnostyczny WebView:", color = PkpTextSecondary, fontSize = 12.sp)
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(260.dp)
                        .clip(RoundedCornerShape(8.dp))
                ) {
                    AndroidView(
                        factory = {
                            (debugWebView.parent as? ViewGroup)?.removeView(debugWebView)
                            debugWebView
                        },
                        modifier = Modifier.fillMaxSize()
                    )
                }
            }
        }
    }
}

@Composable
private fun TrainConnectionCard(
    train: TrainConnection,
    enabled: Boolean,
    onScanClick: () -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = PkpSurface),
        border = BorderStroke(1.dp, PkpBorder),
        shape = RoundedCornerShape(12.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = train.trainNumber,
                    color = PkpOrange,
                    fontWeight = FontWeight.Bold,
                    fontSize = 14.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Text(
                    text = "${train.departure} -> ${train.arrival}" +
                        (if (train.duration.isNotBlank()) " (${train.duration})" else ""),
                    color = PkpTextPrimary,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 15.sp
                )
                if (train.changes > 0) {
                    Text(
                        text = "Połączenie z przesiadką (${SeatOptimizer.formatTransfersPl(train.changes)})",
                        color = PkpTextSecondary,
                        fontSize = 11.sp
                    )
                }
            }
            Spacer(modifier = Modifier.width(8.dp))
            Button(
                onClick = onScanClick,
                enabled = enabled,
                colors = ButtonDefaults.buttonColors(containerColor = PkpEmerald),
                shape = RoundedCornerShape(8.dp)
            ) {
                Text("Skanuj", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 13.sp)
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ResultsSection(
    trainNumber: String,
    fastPathUsed: Boolean,
    profile: OptimizationProfile,
    onProfileChange: (OptimizationProfile) -> Unit,
    strategies: List<OptimizationStrategy>,
    selectedIdx: Int,
    onSelectStrategy: (Int) -> Unit,
    selectedStrategy: OptimizationStrategy,
    onOpenBooking: (String) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "Wyniki dla $trainNumber",
                fontWeight = FontWeight.Bold,
                fontSize = 16.sp,
                color = PkpTextPrimary
            )
            if (fastPathUsed) {
                Surface(
                    color = PkpEmerald.copy(alpha = 0.2f),
                    border = BorderStroke(1.dp, PkpEmerald),
                    shape = RoundedCornerShape(6.dp)
                ) {
                    Text(
                        text = "Fast-Path",
                        color = PkpEmerald,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp)
                    )
                }
            }
        }

        // Optimization profile selector
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            OptimizationProfile.entries.forEach { p ->
                FilterChip(
                    selected = profile == p,
                    onClick = { onProfileChange(p) },
                    label = { Text(p.label, fontSize = 12.sp) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = PkpSurfaceAlt,
                        selectedLabelColor = PkpOrange,
                        containerColor = PkpSurface,
                        labelColor = PkpTextSecondary
                    )
                )
            }
        }

        // Strategy selector tabs
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            strategies.forEachIndexed { idx, s ->
                val isSelected = idx == selectedIdx
                Surface(
                    color = if (isSelected) PkpOrange else PkpSurface,
                    border = BorderStroke(1.dp, if (isSelected) PkpOrange else PkpBorder),
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.clickable { onSelectStrategy(idx) }
                ) {
                    Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                        Text(
                            text = "Wariant ${idx + 1}",
                            fontWeight = FontWeight.Bold,
                            color = if (isSelected) Color.White else PkpOrange,
                            fontSize = 12.sp
                        )
                        Text(
                            text = s.title,
                            color = if (isSelected) Color.White else PkpTextPrimary,
                            fontSize = 12.sp,
                            maxLines = 1
                        )
                    }
                }
            }
        }

        // Selected Variant Summary Card (replaces heavy full-train matrix per user request)
        Card(
            colors = CardDefaults.cardColors(containerColor = PkpSurface),
            border = BorderStroke(1.dp, PkpBorder),
            shape = RoundedCornerShape(14.dp)
        ) {
            Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    text = selectedStrategy.title,
                    fontWeight = FontWeight.Bold,
                    color = PkpTextPrimary,
                    fontSize = 15.sp
                )
                FlowRow(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(
                        text = "Przesiadki: ${SeatOptimizer.formatTransfersPl(selectedStrategy.transfersCount)}",
                        color = PkpTextSecondary,
                        fontSize = 12.sp
                    )
                    Text(
                        text = "Korytarz: ${selectedStrategy.totalStandingMin} min",
                        color = if (selectedStrategy.totalStandingMin > 0) PkpOrange else PkpEmerald,
                        fontSize = 12.sp
                    )
                    Text(
                        text = "Bilety: ${selectedStrategy.steps.count { !it.isStanding }}",
                        color = PkpTextSecondary,
                        fontSize = 12.sp
                    )
                }

                HorizontalDivider(color = PkpBorder)

                Text(
                    text = "Miejsca z wybranego wariantu",
                    fontWeight = FontWeight.SemiBold,
                    color = PkpOrange,
                    fontSize = 13.sp
                )

                selectedStrategy.steps.forEachIndexed { idx, step ->
                    StepCard(step = step, stepNum = idx + 1, onOpenBooking = onOpenBooking)
                }
            }
        }
    }
}

@Composable
private fun StepCard(
    step: PathStep,
    stepNum: Int,
    onOpenBooking: (String) -> Unit
) {
    val accent = if (step.isStanding) PkpOrange else PkpEmerald
    Surface(
        color = PkpSurfaceAlt,
        border = BorderStroke(1.dp, accent.copy(alpha = 0.5f)),
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "$stepNum. ${step.fromStationName} -> ${step.toStationName}",
                    fontWeight = FontWeight.Bold,
                    color = PkpTextPrimary,
                    fontSize = 14.sp,
                    modifier = Modifier.weight(1f)
                )
                Text(
                    text = "${step.durationMin} min",
                    color = PkpTextSecondary,
                    fontSize = 12.sp
                )
            }

            if (step.isStanding) {
                Text(
                    text = "KORYTARZ - brak wolnego miejsca siedzącego na tym odcinku",
                    color = PkpOrange,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 13.sp
                )
            } else {
                val seatSummary = buildString {
                    append("Wagon ${step.carriageNo}, Miejsce ${step.seatNo}")
                    if (step.compartment != null) append(" (przedział/rząd ${step.compartment})")
                }
                Text(
                    text = seatSummary,
                    color = PkpEmerald,
                    fontWeight = FontWeight.Bold,
                    fontSize = 13.sp
                )
                if (step.alternativeSeatsInSameCarriage.isNotEmpty()) {
                    Text(
                        text = "Alternatywne w Wagonie ${step.carriageNo}: ${
                            step.alternativeSeatsInSameCarriage.take(6).joinToString(", ") { "m$it" }
                        }",
                        color = PkpTextSecondary,
                        fontSize = 11.sp
                    )
                }
            }

            if (!step.transferInstruction.isNullOrBlank()) {
                Text(
                    text = step.transferInstruction,
                    color = PkpTextSecondary,
                    fontSize = 12.sp
                )
            }

            if (!step.bookingUrl.isNullOrBlank() && !step.isStanding) {
                Button(
                    onClick = { onOpenBooking(step.bookingUrl) },
                    colors = ButtonDefaults.buttonColors(containerColor = PkpOrange),
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Kup bilet na e-IC", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                }
            }
        }
    }
}

private fun openUrlSafe(context: Context, url: String) {
    try {
        val uri = Uri.parse(url)
        CustomTabsIntent.Builder()
            .setShowTitle(true)
            .build()
            .launchUrl(context, uri)
    } catch (_: Exception) {
        try {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: Exception) {
        }
    }
}
