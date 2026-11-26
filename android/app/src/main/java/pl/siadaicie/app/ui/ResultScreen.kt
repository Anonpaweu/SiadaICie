package pl.siadaicie.app.ui

import android.view.ViewGroup
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.HorizontalDivider
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.DirectionsBike
import androidx.compose.material.icons.automirrored.filled.DirectionsWalk
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.AccessibilityNew
import androidx.compose.material.icons.filled.Accessible
import androidx.compose.material.icons.filled.AirlineSeatReclineNormal
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ChildFriendly
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Elderly
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.FilterList
import androidx.compose.material.icons.filled.GridOn
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.LocalCafe
import androidx.compose.material.icons.filled.PregnantWoman
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import kotlinx.coroutines.delay
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.layout
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import pl.siadaicie.app.domain.FullTrainAvailabilityMap
import pl.siadaicie.app.domain.SeatFilterPrefs
import pl.siadaicie.app.optimizer.OptimizationProfile
import pl.siadaicie.app.optimizer.OptimizationStrategy
import pl.siadaicie.app.optimizer.PathStep
import pl.siadaicie.app.optimizer.SeatOptimizer
import pl.siadaicie.app.scraper.GrmParser
import pl.siadaicie.app.service.ScanProgressState

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ResultScreen(
    ui: SearchUiState,
    scan: ScanProgressState,
    onBack: () -> Unit,
    onCancelScan: () -> Unit,
    onRetryScan: () -> Unit,
    onProfileChange: (OptimizationProfile) -> Unit,
    onSeatFiltersChange: (SeatFilterPrefs) -> Unit,
    onSelectStrategy: (Int) -> Unit,
    onOpenBooking: (String) -> Unit,
    onOpenSupport: () -> Unit
) {
    val colors = LocalRailColors.current
    val isNoSeatmap = scan.noSeatmapMode || (scan.availabilityMap?.noSeatmapMode == true)
    val fullRouteStandingStatus = scan.fullRouteStandingStatus ?: scan.availabilityMap?.fullRouteStandingStatus
    var showLogs by remember { mutableStateOf(false) }
    var showResultFilters by remember { mutableStateOf(false) }
    var showSeatGrid by remember { mutableStateOf(false) }
    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }

    LaunchedEffect(scan.loadedFromHistoryAtMs) {
        if (scan.loadedFromHistoryAtMs != null) {
            nowMs = System.currentTimeMillis()
            while (true) {
                delay(30_000L)
                nowMs = System.currentTimeMillis()
            }
        }
    }

    Scaffold(
        containerColor = colors.canvas,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = colors.card,
                    titleContentColor = colors.textPrimary
                ),
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Powrót",
                            tint = colors.textPrimary
                        )
                    }
                },
                title = {
                    val headerFrom = scan.fromStation.ifBlank { ui.fromQuery }
                    val headerTo = scan.toStation.ifBlank { ui.toQuery }
                    val headerDate = scan.travelDate.ifBlank { ui.travelDate }
                    Column {
                        Text(
                            text = "$headerFrom -> $headerTo",
                            fontWeight = FontWeight.Bold,
                            fontSize = 15.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        val trainTitle = scan.trainNumber.ifBlank { ui.lastScannedTrain?.trainNumber.orEmpty() }
                        Text(
                            text = "$trainTitle • ${formatDatePl(headerDate)} • Klasa ${scan.ticketClass}",
                            fontSize = 12.sp,
                            color = colors.textSecondary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            )
        }
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(top = padding.calculateTopPadding()),
            contentPadding = PaddingValues(
                start = 16.dp,
                end = 16.dp,
                top = 16.dp,
                bottom = padding.calculateBottomPadding() + 16.dp
            ),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            if (scan.isRunning) {
                item(key = "scanning_card") {
                    Surface(
                        color = colors.card,
                        shape = RoundedCornerShape(24.dp),
                        shadowElevation = if (colors.isDark) 0.dp else 2.dp,
                        border = if (colors.isDark) BorderStroke(1.dp, colors.hairline) else null,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(
                            modifier = Modifier.padding(20.dp),
                            verticalArrangement = Arrangement.spacedBy(14.dp)
                        ) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.Bottom
                            ) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        text = "Sprawdzanie wolnych miejsc",
                                        fontSize = 13.sp,
                                        color = colors.textSecondary,
                                        fontWeight = FontWeight.Medium
                                    )
                                    Text(
                                        text = scan.trainNumber,
                                        fontSize = 20.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = colors.textPrimary
                                    )
                                }
                                Text(
                                    text = "${scan.progress}%",
                                    style = TextStyle(
                                        fontSize = 28.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFeatureSettings = "tnum",
                                        color = colors.accent
                                    )
                                )
                            }

                            LinearProgressIndicator(
                                progress = { (scan.progress / 100f).coerceIn(0f, 1f) },
                                color = colors.accent,
                                trackColor = colors.cardTonal,
                                strokeCap = StrokeCap.Round,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(8.dp)
                            )

                            Text(
                                text = scan.statusMessage.ifBlank { "Łączenie z systemem rezerwacyjnym..." },
                                color = colors.textPrimary,
                                fontSize = 14.sp
                            )

                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(colors.cardTonal.copy(alpha = 0.65f))
                                    .padding(horizontal = 10.dp, vertical = 8.dp)
                            ) {
                                Icon(
                                    imageVector = Icons.Default.Info,
                                    contentDescription = null,
                                    tint = colors.textSecondary,
                                    modifier = Modifier.size(15.dp)
                                )
                                Text(
                                    text = "Możesz wyjść z aplikacji — skanowanie będzie trwać w tle.",
                                    color = colors.textSecondary,
                                    fontSize = 12.sp,
                                    lineHeight = 16.sp
                                )
                            }

                            Surface(
                                color = colors.cardTonal,
                                shape = RoundedCornerShape(12.dp),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable { showLogs = !showLogs }
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Text(
                                        text = "Dziennik skanowania (${scan.logs.size})",
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.Medium,
                                        color = colors.textSecondary
                                    )
                                    Icon(
                                        imageVector = if (showLogs) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                                        contentDescription = null,
                                        tint = colors.textSecondary,
                                        modifier = Modifier.size(18.dp)
                                    )
                                }
                            }

                            AnimatedVisibility(visible = showLogs) {
                                Surface(
                                    color = colors.canvas,
                                    shape = RoundedCornerShape(12.dp),
                                    border = BorderStroke(1.dp, colors.hairline),
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .heightIn(max = 180.dp)
                                ) {
                                    Column(
                                        modifier = Modifier
                                            .verticalScroll(rememberScrollState())
                                            .padding(10.dp),
                                        verticalArrangement = Arrangement.spacedBy(3.dp)
                                    ) {
                                        for (line in scan.logs) {
                                            Text(line, color = colors.textSecondary, fontSize = 11.sp)
                                        }
                                    }
                                }
                            }

                            val debugWv = if (ui.debugWebViewEnabled) scan.debugWebView else null
                            if (debugWv != null) {
                                Text(
                                    text = "Podgląd diagnostyczny WebView",
                                    fontSize = 12.sp,
                                    color = colors.textSecondary,
                                    fontWeight = FontWeight.Medium
                                )
                                Box(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(250.dp)
                                        .clip(RoundedCornerShape(12.dp))
                                        .border(1.dp, colors.hairline, RoundedCornerShape(12.dp))
                                ) {
                                    AndroidView(
                                        factory = {
                                            (debugWv.parent as? ViewGroup)?.removeView(debugWv)
                                            debugWv
                                        },
                                        modifier = Modifier.fillMaxSize()
                                    )
                                }
                            }

                            OutlinedButton(
                                onClick = onCancelScan,
                                shape = RoundedCornerShape(14.dp),
                                border = BorderStroke(1.dp, colors.errorRed.copy(alpha = 0.5f)),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(46.dp)
                            ) {
                                Text("Anuluj skanowanie", color = colors.errorRed, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
            }

            if (!scan.isRunning && scan.error != null) {
                item(key = "error_card") {
                    Surface(
                        color = colors.errorContainer,
                        shape = RoundedCornerShape(22.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(
                            modifier = Modifier.padding(18.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp)
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(
                                    imageVector = Icons.Default.ErrorOutline,
                                    contentDescription = null,
                                    tint = colors.errorRed,
                                    modifier = Modifier.size(22.dp)
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    text = "Nie udało się dokończyć skanu",
                                    fontWeight = FontWeight.Bold,
                                    fontSize = 16.sp,
                                    color = colors.errorRed
                                )
                            }
                            Text(
                                text = scan.error,
                                fontSize = 14.sp,
                                color = colors.textPrimary
                            )
                            if (scan.logs.isNotEmpty()) {
                                Surface(
                                    color = colors.card.copy(alpha = 0.7f),
                                    shape = RoundedCornerShape(12.dp),
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .clickable { showLogs = !showLogs }
                                ) {
                                    Row(
                                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                                        horizontalArrangement = Arrangement.SpaceBetween,
                                        verticalAlignment = Alignment.CenterVertically
                                    ) {
                                        Text(
                                            text = "Dziennik skanowania (${scan.logs.size})",
                                            fontSize = 12.sp,
                                            fontWeight = FontWeight.Medium,
                                            color = colors.textSecondary
                                        )
                                        Icon(
                                            imageVector = if (showLogs) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                                            contentDescription = null,
                                            tint = colors.textSecondary,
                                            modifier = Modifier.size(18.dp)
                                        )
                                    }
                                }
                                AnimatedVisibility(visible = showLogs) {
                                    Surface(
                                        color = colors.canvas,
                                        shape = RoundedCornerShape(12.dp),
                                        border = BorderStroke(1.dp, colors.hairline),
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .heightIn(max = 180.dp)
                                    ) {
                                        Column(
                                            modifier = Modifier
                                                .verticalScroll(rememberScrollState())
                                                .padding(10.dp),
                                            verticalArrangement = Arrangement.spacedBy(3.dp)
                                        ) {
                                            for (line in scan.logs) {
                                                Text(line, color = colors.textSecondary, fontSize = 11.sp)
                                            }
                                        }
                                    }
                                }
                            }
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                Button(
                                    onClick = onRetryScan,
                                    colors = ButtonDefaults.buttonColors(containerColor = colors.accent),
                                    shape = RoundedCornerShape(12.dp),
                                    modifier = Modifier.weight(1f)
                                ) {
                                    Icon(Icons.Default.Refresh, contentDescription = null, modifier = Modifier.size(16.dp))
                                    Spacer(modifier = Modifier.width(6.dp))
                                    Text("Ponów")
                                }
                                OutlinedButton(
                                    onClick = onBack,
                                    shape = RoundedCornerShape(12.dp),
                                    modifier = Modifier.weight(1f)
                                ) {
                                    Text("Wróć do listy", color = colors.textPrimary)
                                }
                            }
                        }
                    }
                }
            }

            if (!scan.isRunning && scan.error == null && scan.strategies.isEmpty()) {
                item(key = "empty_fallback") {
                    Surface(
                        color = colors.card,
                        shape = RoundedCornerShape(22.dp),
                        border = BorderStroke(1.dp, colors.hairline),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(
                            modifier = Modifier.padding(18.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp)
                        ) {
                            Text(
                                text = "Brak gotowych wariantów do wyświetlenia",
                                fontWeight = FontWeight.Bold,
                                fontSize = 16.sp,
                                color = colors.textPrimary
                            )
                            Text(
                                text = scan.statusMessage.ifBlank {
                                    "Skanowanie nie zwróciło żadnego wariantu z miejscem siedzącym lub zostało przerwane."
                                },
                                fontSize = 14.sp,
                                color = colors.textSecondary
                            )
                            val isEipEmpty = Regex("^EIP\\b", RegexOption.IGNORE_CASE)
                                .containsMatchIn(scan.trainNumber.trim())
                            val isOverbookedFull = fullRouteStandingStatus == "OVERBOOKED"
                            val isSpecialOnlyFull = fullRouteStandingStatus == "SPECIAL_ONLY"
                            if (isOverbookedFull && !isEipEmpty) {
                                Surface(
                                    color = colors.errorContainer,
                                    shape = RoundedCornerShape(12.dp),
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Text(
                                        text = "PKP Intercity zablokowało sprzedaż w wybranej klasie (brak miejsc siedzących i stojących). Sprawdź 1. klasę lub inny pociąg.",
                                        color = colors.errorRed,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.Medium,
                                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp)
                                    )
                                }
                            } else if (isSpecialOnlyFull && !isEipEmpty) {
                                Surface(
                                    color = colors.corridorAmberContainer,
                                    shape = RoundedCornerShape(12.dp),
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Text(
                                        text = "Na całej trasie e-IC nie sprzedaje zwykłych biletów ani biletów bez gwarancji — dostępne są wyłącznie miejsca specjalne (dla uprawnionych) lub 1. klasa.",
                                        color = colors.corridorAmber,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.Medium,
                                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp)
                                    )
                                }
                                if (!scan.fullRouteBookingUrl.isNullOrBlank()) {
                                    OutlinedButton(
                                        onClick = { onOpenBooking(scan.fullRouteBookingUrl) },
                                        shape = RoundedCornerShape(12.dp),
                                        border = BorderStroke(1.dp, colors.corridorAmber.copy(alpha = 0.7f)),
                                        modifier = Modifier.fillMaxWidth()
                                    ) {
                                        Text(
                                            text = "Sprawdź miejsca specjalne / 1. klasę (e-IC)",
                                            color = colors.corridorAmber,
                                            fontWeight = FontWeight.SemiBold,
                                            fontSize = 13.sp
                                        )
                                        Spacer(modifier = Modifier.width(6.dp))
                                        Icon(
                                            imageVector = Icons.AutoMirrored.Filled.OpenInNew,
                                            contentDescription = null,
                                            tint = colors.corridorAmber,
                                            modifier = Modifier.size(15.dp)
                                        )
                                    }
                                }
                            } else if (scan.strategies.isEmpty() && !isEipEmpty && !scan.fullRouteBookingUrl.isNullOrBlank()) {
                                OutlinedButton(
                                    onClick = { onOpenBooking(scan.fullRouteBookingUrl) },
                                    shape = RoundedCornerShape(12.dp),
                                    border = BorderStroke(1.dp, colors.corridorAmber.copy(alpha = 0.7f)),
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Text(
                                        text = "Sprawdź bilet bez gwarancji w e-IC",
                                        color = colors.corridorAmber,
                                        fontWeight = FontWeight.SemiBold,
                                        fontSize = 13.sp
                                    )
                                    Spacer(modifier = Modifier.width(6.dp))
                                    Icon(
                                        imageVector = Icons.AutoMirrored.Filled.OpenInNew,
                                        contentDescription = null,
                                        tint = colors.corridorAmber,
                                        modifier = Modifier.size(15.dp)
                                    )
                                }
                            }
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                Button(
                                    onClick = onRetryScan,
                                    colors = ButtonDefaults.buttonColors(containerColor = colors.accent),
                                    shape = RoundedCornerShape(12.dp),
                                    modifier = Modifier.weight(1f)
                                ) {
                                    Icon(Icons.Default.Refresh, contentDescription = null, modifier = Modifier.size(16.dp))
                                    Spacer(modifier = Modifier.width(6.dp))
                                    Text("Ponów")
                                }
                                OutlinedButton(
                                    onClick = onBack,
                                    shape = RoundedCornerShape(12.dp),
                                    modifier = Modifier.weight(1f)
                                ) {
                                    Text("Wróć do listy", color = colors.textPrimary)
                                }
                            }
                        }
                    }
                }
            }

            if (!scan.isRunning && isNoSeatmap) {
                item(key = "no_seatmap_banner") {
                    Surface(
                        color = colors.cardTonal,
                        shape = RoundedCornerShape(16.dp),
                        border = BorderStroke(1.dp, colors.brandNavy.copy(alpha = 0.28f)),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                            verticalAlignment = Alignment.Top
                        ) {
                            Icon(
                                imageVector = Icons.Default.Info,
                                contentDescription = null,
                                tint = colors.brandNavy,
                                modifier = Modifier
                                    .padding(top = 2.dp)
                                    .size(18.dp)
                            )
                            Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                                Text(
                                    text = "Pociąg bez schematu miejsc (przydział automatyczny)",
                                    fontWeight = FontWeight.Bold,
                                    fontSize = 13.sp,
                                    color = colors.textPrimary
                                )
                                Text(
                                    text = "Ten pociąg nie ma graficznej mapy miejsc w e-IC. System sprawdził odcinki bez blokowania biletów i znalazł najdłuższe fragmenty z gwarantowanym miejscem siedzącym.",
                                    fontSize = 12.sp,
                                    lineHeight = 16.sp,
                                    color = colors.textSecondary
                                )
                            }
                        }
                    }
                }
            }

            if (!scan.isRunning && scan.availabilityMap != null && !isNoSeatmap) {
                item(key = "result_filters") {
                    Surface(
                        color = colors.card,
                        shape = RoundedCornerShape(16.dp),
                        border = BorderStroke(1.dp, colors.hairline),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(modifier = Modifier.fillMaxWidth()) {
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable { showResultFilters = !showResultFilters }
                                    .padding(horizontal = 14.dp, vertical = 10.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.FilterList,
                                        contentDescription = null,
                                        tint = colors.brandNavy,
                                        modifier = Modifier.size(16.dp)
                                    )
                                    Text(
                                        text = "Filtry miejsc (${ui.seatFilters.activeSpecialCount()}/6)",
                                        fontWeight = FontWeight.SemiBold,
                                        fontSize = 13.sp,
                                        color = colors.textPrimary
                                    )
                                }
                                Icon(
                                    imageVector = if (showResultFilters) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                                    contentDescription = null,
                                    tint = colors.textSecondary
                                )
                            }
                            AnimatedVisibility(visible = showResultFilters) {
                                Column(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(start = 14.dp, end = 14.dp, bottom = 12.dp),
                                    verticalArrangement = Arrangement.spacedBy(10.dp)
                                ) {
                                    HorizontalDivider(color = colors.hairline)
                                    SeatFilterChipsSection(
                                        filters = ui.seatFilters,
                                        onFiltersChange = onSeatFiltersChange
                                    )
                                }
                            }
                        }
                    }
                }
            }

            if (!scan.isRunning && scan.loadedFromHistoryAtMs != null) {
                item(key = "history_expiry_strip") {
                    Surface(
                        color = colors.cardTonal,
                        shape = RoundedCornerShape(14.dp),
                        border = BorderStroke(1.dp, colors.hairline),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 14.dp, vertical = 9.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = "Wynik z historii • ${formatHistoryRemainingPl(scan.loadedFromHistoryAtMs, nowMs)}",
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.Medium,
                                    color = colors.textPrimary
                                )
                                Text(
                                    text = "Dostępność ostatnich miejsc może się szybko zmienić",
                                    fontSize = 11.sp,
                                    color = colors.textSecondary
                                )
                            }
                            if (ui.lastScannedTrain != null) {
                                androidx.compose.material3.TextButton(onClick = onRetryScan) {
                                    Icon(
                                        imageVector = Icons.Default.Refresh,
                                        contentDescription = null,
                                        tint = colors.accent,
                                        modifier = Modifier.size(16.dp)
                                    )
                                    Spacer(modifier = Modifier.width(4.dp))
                                    Text(
                                        text = "Skanuj ponownie",
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        color = colors.accent
                                    )
                                }
                            }
                        }
                    }
                }
            }

            if (!scan.isRunning && !isNoSeatmap && !scan.bikeSuggestion.isNullOrBlank() && !ui.seatFilters.allowBike) {
                item(key = "bike_suggestion_banner") {
                    Surface(
                        color = colors.seatEmeraldContainer,
                        shape = RoundedCornerShape(16.dp),
                        border = BorderStroke(1.dp, colors.seatEmerald.copy(alpha = 0.35f)),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 14.dp, vertical = 11.dp),
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(
                                imageVector = Icons.AutoMirrored.Filled.DirectionsBike,
                                contentDescription = null,
                                tint = colors.seatEmerald,
                                modifier = Modifier.size(20.dp)
                            )
                            Column(
                                modifier = Modifier.weight(1f),
                                verticalArrangement = Arrangement.spacedBy(2.dp)
                            ) {
                                Text(
                                    text = "Podpowiedź: włącz filtr Rower",
                                    fontWeight = FontWeight.Bold,
                                    fontSize = 13.sp,
                                    color = colors.textPrimary
                                )
                                Text(
                                    text = scan.bikeSuggestion,
                                    fontSize = 12.sp,
                                    lineHeight = 16.sp,
                                    color = colors.textSecondary
                                )
                            }
                            Button(
                                onClick = { onSeatFiltersChange(ui.seatFilters.copy(allowBike = true)) },
                                colors = ButtonDefaults.buttonColors(
                                    containerColor = colors.seatEmerald,
                                    contentColor = Color.White
                                ),
                                shape = RoundedCornerShape(10.dp),
                                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
                            ) {
                                Text("Włącz Rower", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
            }

            if (!scan.isRunning && scan.strategies.isNotEmpty()) {
                val safeIdx = ui.selectedStrategyIdx.coerceIn(0, scan.strategies.lastIndex)
                val selectedStrategy = scan.strategies[safeIdx]
                val isGoldenShot = selectedStrategy.transfersCount == 0 && selectedStrategy.totalStandingMin == 0

                // Hero Summary Banner
                item(key = "hero_summary") {
                    Surface(
                        color = if (isGoldenShot) colors.seatEmeraldContainer else colors.card,
                        shape = RoundedCornerShape(22.dp),
                        shadowElevation = if (colors.isDark || isGoldenShot) 0.dp else 2.dp,
                        border = if (isGoldenShot) {
                            BorderStroke(1.dp, colors.seatEmerald.copy(alpha = 0.35f))
                        } else if (colors.isDark) {
                            BorderStroke(1.dp, colors.hairline)
                        } else null,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(
                            modifier = Modifier.padding(18.dp),
                            verticalArrangement = Arrangement.spacedBy(10.dp)
                        ) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    modifier = Modifier.weight(1f)
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.CheckCircle,
                                        contentDescription = null,
                                        tint = if (isGoldenShot) colors.seatEmerald else colors.brandNavy,
                                        modifier = Modifier.size(20.dp)
                                    )
                                    Spacer(modifier = Modifier.width(8.dp))
                                    Text(
                                        text = selectedStrategy.title,
                                        fontWeight = FontWeight.Bold,
                                        fontSize = 16.sp,
                                        color = colors.textPrimary
                                    )
                                }
                                if (scan.fastPathUsed) {
                                    Surface(
                                        color = colors.seatEmerald.copy(alpha = 0.16f),
                                        shape = RoundedCornerShape(8.dp)
                                    ) {
                                        Row(
                                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                            verticalAlignment = Alignment.CenterVertically
                                        ) {
                                            Icon(
                                                imageVector = Icons.Default.Bolt,
                                                contentDescription = null,
                                                tint = colors.seatEmerald,
                                                modifier = Modifier.size(13.dp)
                                            )
                                            Spacer(modifier = Modifier.width(3.dp))
                                            Text(
                                                text = "Fast-Path",
                                                color = colors.seatEmerald,
                                                fontSize = 11.sp,
                                                fontWeight = FontWeight.Bold
                                            )
                                        }
                                    }
                                }
                            }

                            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                MetricPill(
                                    label = SeatOptimizer.formatTransfersPl(selectedStrategy.transfersCount).replaceFirstChar { it.uppercase() },
                                    fg = colors.textPrimary,
                                    bg = colors.card.copy(alpha = if (isGoldenShot) 0.7f else 1f)
                                )
                                MetricPill(
                                    label = "Korytarz: ${selectedStrategy.totalStandingMin} min",
                                    fg = if (selectedStrategy.totalStandingMin > 0) colors.corridorAmber else colors.seatEmerald,
                                    bg = if (selectedStrategy.totalStandingMin > 0) colors.corridorAmberContainer else colors.cardTonal
                                )
                                val ticketsCount = selectedStrategy.steps.count { !it.isStanding }
                                MetricPill(
                                    label = "Biletów: $ticketsCount",
                                    fg = colors.textSecondary,
                                    bg = colors.cardTonal
                                )
                            }
                        }
                    }
                }

                item(key = "support_coffee_bar") {
                    Surface(
                        onClick = onOpenSupport,
                        color = colors.card,
                        shape = RoundedCornerShape(14.dp),
                        border = BorderStroke(1.dp, colors.accent.copy(alpha = 0.35f)),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 14.dp, vertical = 10.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(10.dp)
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(28.dp)
                                        .clip(RoundedCornerShape(8.dp))
                                        .background(colors.accentContainer),
                                    contentAlignment = Alignment.Center
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.LocalCafe,
                                        contentDescription = null,
                                        tint = colors.accent,
                                        modifier = Modifier.size(16.dp)
                                    )
                                }
                                Text(
                                    text = "Pomogłem? Buy me a coffee :D",
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    color = colors.textPrimary
                                )
                            }
                            Icon(
                                imageVector = Icons.AutoMirrored.Filled.OpenInNew,
                                contentDescription = "Otwórz stronę wsparcia",
                                tint = colors.accent,
                                modifier = Modifier.size(16.dp)
                            )
                        }
                    }
                }

                // Optimization profile + variant switchers
                if (!isNoSeatmap || scan.strategies.size > 1) {
                    item(key = "selectors") {
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            if (!isNoSeatmap) {
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .horizontalScroll(rememberScrollState()),
                                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                                ) {
                                    OptimizationProfile.entries.forEach { p ->
                                        FilterChip(
                                            selected = ui.profile == p,
                                            onClick = { onProfileChange(p) },
                                            label = { Text(p.label, fontSize = 12.sp) },
                                            colors = FilterChipDefaults.filterChipColors(
                                                selectedContainerColor = colors.brandNavy,
                                                selectedLabelColor = Color.White,
                                                containerColor = colors.card,
                                                labelColor = colors.textSecondary
                                            ),
                                            border = FilterChipDefaults.filterChipBorder(
                                                enabled = true,
                                                selected = ui.profile == p,
                                                borderColor = colors.hairline
                                            )
                                        )
                                    }
                                }
                            }

                            if (scan.strategies.size > 1) {
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .horizontalScroll(rememberScrollState()),
                                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                                ) {
                                    scan.strategies.forEachIndexed { idx, s ->
                                        val isSel = idx == safeIdx
                                        Surface(
                                            onClick = { onSelectStrategy(idx) },
                                            color = if (isSel) colors.accent else colors.card,
                                            shape = RoundedCornerShape(14.dp),
                                            border = if (!isSel) BorderStroke(1.dp, colors.hairline) else null
                                        ) {
                                            Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 9.dp)) {
                                                Text(
                                                    text = "Wariant ${idx + 1}",
                                                    fontSize = 11.sp,
                                                    fontWeight = FontWeight.Bold,
                                                    color = if (isSel) Color.White.copy(alpha = 0.85f) else colors.accent
                                                )
                                                Text(
                                                    text = s.title,
                                                    fontSize = 12.sp,
                                                    fontWeight = FontWeight.SemiBold,
                                                    color = if (isSel) Color.White else colors.textPrimary,
                                                    maxLines = 1
                                                )
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Timeline header: Selected Variant Only
                item(key = "timeline_header") {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 4.dp, start = 2.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "Miejsca z wybranego wariantu",
                            fontWeight = FontWeight.Bold,
                            fontSize = 15.sp,
                            color = colors.textPrimary,
                            modifier = Modifier.weight(1f)
                        )
                        val gridMap = scan.availabilityMap
                        if (!isNoSeatmap && gridMap != null) {
                            Surface(
                                onClick = { showSeatGrid = true },
                                color = colors.cardTonal,
                                shape = RoundedCornerShape(10.dp),
                                border = BorderStroke(1.dp, colors.brandNavy.copy(alpha = 0.25f))
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(5.dp)
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.GridOn,
                                        contentDescription = null,
                                        tint = colors.brandNavy,
                                        modifier = Modifier.size(15.dp)
                                    )
                                    Text(
                                        text = "Siatka miejsc",
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        color = colors.brandNavy
                                    )
                                }
                            }
                        }
                    }
                }

                item(key = "timeline_steps") {
                    val isEipTrain = Regex("^EIP\\b", RegexOption.IGNORE_CASE)
                        .containsMatchIn(scan.trainNumber.trim())
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        JourneyTimeline(
                            strategy = selectedStrategy,
                            isEipTrain = isEipTrain,
                            onOpenBooking = onOpenBooking
                        )
                        if (
                            selectedStrategy.totalStandingMin > 0 &&
                            !isEipTrain &&
                            !scan.fullRouteBookingUrl.isNullOrBlank() &&
                            fullRouteStandingStatus != "OVERBOOKED" &&
                            fullRouteStandingStatus != "SPECIAL_ONLY"
                        ) {
                            OutlinedButton(
                                onClick = { onOpenBooking(scan.fullRouteBookingUrl) },
                                shape = RoundedCornerShape(14.dp),
                                border = BorderStroke(1.dp, colors.corridorAmber.copy(alpha = 0.65f)),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(44.dp)
                            ) {
                                Text(
                                    text = "Alternatywa: 1 bilet bez gwarancji (cała trasa)",
                                    color = colors.corridorAmber,
                                    fontWeight = FontWeight.SemiBold,
                                    fontSize = 13.sp
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Icon(
                                    imageVector = Icons.AutoMirrored.Filled.OpenInNew,
                                    contentDescription = null,
                                    tint = colors.corridorAmber,
                                    modifier = Modifier.size(15.dp)
                                )
                            }
                        }
                    }
                }
            }
        }

        val modalMap = scan.availabilityMap
        if (showSeatGrid && !isNoSeatmap && modalMap != null) {
            SeatAvailabilityGridDialog(
                map = modalMap,
                seatFilters = ui.seatFilters,
                onDismiss = { showSeatGrid = false }
            )
        }
    }
}

@Composable
private fun MetricPill(label: String, fg: Color, bg: Color) {
    Surface(color = bg, shape = RoundedCornerShape(8.dp)) {
        Text(
            text = label,
            color = fg,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
            modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp)
        )
    }
}

@Composable
private fun JourneyTimeline(
    strategy: OptimizationStrategy,
    isEipTrain: Boolean,
    onOpenBooking: (String) -> Unit
) {
    val colors = LocalRailColors.current
    Column(verticalArrangement = Arrangement.spacedBy(0.dp)) {
        strategy.steps.forEachIndexed { idx, step ->
            val isLast = idx == strategy.steps.lastIndex
            val railColor = when {
                step.isStanding && step.standingStatus == "OVERBOOKED" -> colors.errorRed
                step.isStanding -> colors.corridorAmber
                else -> colors.seatEmerald
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(IntrinsicSize.Min)
            ) {
                // Vertical transit rail column
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier
                        .width(28.dp)
                        .fillMaxHeight()
                ) {
                    Spacer(modifier = Modifier.height(14.dp))
                    Box(
                        modifier = Modifier
                            .size(12.dp)
                            .background(railColor, CircleShape)
                            .border(2.dp, colors.canvas, CircleShape)
                    )
                    if (!isLast) {
                        Box(
                            modifier = Modifier
                                .width(3.dp)
                                .weight(1f)
                                .background(railColor.copy(alpha = 0.55f))
                        )
                    }
                }

                Spacer(modifier = Modifier.width(6.dp))

                TimelineStepCard(
                    step = step,
                    stepNum = idx + 1,
                    isEipTrain = isEipTrain,
                    onOpenBooking = onOpenBooking,
                    modifier = Modifier
                        .weight(1f)
                        .padding(bottom = if (isLast) 0.dp else 12.dp)
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TimelineStepCard(
    step: PathStep,
    stepNum: Int,
    isEipTrain: Boolean,
    onOpenBooking: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    val colors = LocalRailColors.current
    val isOverbookedStep = step.isStanding && step.standingStatus == "OVERBOOKED"
    val badgeBg = when {
        isOverbookedStep -> colors.errorContainer
        step.isStanding -> colors.corridorAmberContainer
        else -> colors.seatEmeraldContainer
    }
    val badgeFg = when {
        isOverbookedStep -> colors.errorRed
        step.isStanding -> colors.corridorAmber
        else -> colors.seatEmerald
    }

    Surface(
        color = colors.card,
        shape = RoundedCornerShape(18.dp),
        shadowElevation = if (colors.isDark) 0.dp else 1.5.dp,
        border = if (colors.isDark) BorderStroke(1.dp, colors.hairline) else null,
        modifier = modifier
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "$stepNum. ${step.fromStationName} -> ${step.toStationName}",
                    fontWeight = FontWeight.Bold,
                    fontSize = 14.sp,
                    color = colors.textPrimary,
                    modifier = Modifier.weight(1f)
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = "${step.durationMin} min",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    color = colors.textSecondary
                )
            }

            Surface(color = badgeBg, shape = RoundedCornerShape(10.dp)) {
                Row(
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 7.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        imageVector = if (step.isStanding) Icons.AutoMirrored.Filled.DirectionsWalk else Icons.Default.AirlineSeatReclineNormal,
                        contentDescription = null,
                        tint = badgeFg,
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(
                        text = when {
                            isOverbookedStep ->
                                "BRAK BILETÓW W TEJ KLASIE (nawet stojących)"
                            step.isStanding && step.standingStatus == "SPECIAL_ONLY" ->
                                "Tylko miejsca specjalne (brak zwykłych i stojących)"
                            step.isStanding ->
                                "KORYTARZ - brak miejsca siedzącego"
                            step.seatNo.startsWith("AUTO") ->
                                "Miejsce siedzące · przydział automatyczny e-IC"
                            else ->
                                "Wagon ${step.carriageNo} · Miejsce ${step.seatNo}" +
                                    (if (step.compartment != null) " (przedział ${step.compartment})" else "")
                        },
                        color = badgeFg,
                        fontWeight = FontWeight.Bold,
                        fontSize = 13.sp
                    )
                }
            }

            if (isOverbookedStep) {
                Text(
                    text = "Na tym odcinku system e-IC zablokował sprzedaż w wybranej klasie. Sprawdź 1. klasę lub inny pociąg.",
                    fontSize = 12.sp,
                    color = colors.errorRed
                )
            } else if (step.isStanding && step.standingStatus == "SPECIAL_ONLY") {
                Text(
                    text = "Na tym odcinku e-IC nie sprzedaje zwykłych biletów ani biletów bez gwarancji miejsca — dostępne są wyłącznie miejsca specjalne (dla uprawnionych) lub 1. klasa.",
                    fontSize = 12.sp,
                    color = colors.textSecondary
                )
            }

            if (!step.isStanding && step.seatTags.isNotEmpty()) {
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    step.seatTags.forEach { tag ->
                        val restricted = GrmParser.isRestrictedTag(tag)
                        Surface(
                            color = if (restricted) colors.corridorAmberContainer else colors.cardTonal,
                            shape = RoundedCornerShape(8.dp)
                        ) {
                            Text(
                                text = GrmParser.formatSeatTagPl(tag),
                                color = if (restricted) colors.corridorAmber else colors.textSecondary,
                                fontSize = 11.sp,
                                fontWeight = if (restricted) FontWeight.SemiBold else FontWeight.Medium,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                            )
                        }
                    }
                }
            }

            if (!step.isStanding && step.alternativeSeatsInSameCarriage.isNotEmpty()) {
                Text(
                    text = "Inne wolne w Wagonie ${step.carriageNo}: ${
                        step.alternativeSeatsInSameCarriage.take(6).joinToString(", ") { "m$it" }
                    }",
                    fontSize = 12.sp,
                    color = colors.textSecondary
                )
            }

            if (!step.transferInstruction.isNullOrBlank()) {
                HorizontalDivider(color = colors.hairline.copy(alpha = 0.7f))
                Text(
                    text = step.transferInstruction,
                    fontSize = 12.sp,
                    color = colors.textSecondary
                )
            }

            if (!step.isStanding && !step.bookingUrl.isNullOrBlank()) {
                Button(
                    onClick = { onOpenBooking(step.bookingUrl) },
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = colors.brandNavy,
                        contentColor = Color.White
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(42.dp)
                ) {
                    Text("Kup bilet na e-IC", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                    Spacer(modifier = Modifier.width(6.dp))
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.OpenInNew,
                        contentDescription = null,
                        modifier = Modifier.size(15.dp)
                    )
                }
            } else if (step.isStanding && !isEipTrain && !isOverbookedStep && !step.bookingUrl.isNullOrBlank()) {
                OutlinedButton(
                    onClick = { onOpenBooking(step.bookingUrl) },
                    shape = RoundedCornerShape(12.dp),
                    border = BorderStroke(1.dp, colors.corridorAmber.copy(alpha = 0.7f)),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(40.dp)
                ) {
                    Text(
                        text = if (step.standingStatus == "SPECIAL_ONLY") {
                            "Sprawdź miejsca specjalne / 1. klasę (e-IC)"
                        } else {
                            "Kup bilet bez gwarancji (e-IC)"
                        },
                        color = colors.corridorAmber,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 12.sp
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.OpenInNew,
                        contentDescription = null,
                        tint = colors.corridorAmber,
                        modifier = Modifier.size(14.dp)
                    )
                }
            }
        }
    }
}

private fun Modifier.verticalRotated(): Modifier = layout { measurable, constraints ->
    val placeable = measurable.measure(
        constraints.copy(
            minWidth = 0,
            maxWidth = constraints.maxHeight,
            minHeight = 0,
            maxHeight = constraints.maxWidth
        )
    )
    layout(placeable.height, placeable.width) {
        placeable.place(
            x = -(placeable.width / 2 - placeable.height / 2),
            y = placeable.width / 2 - placeable.height / 2
        )
    }
}

private fun abbreviateStationForGrid(name: String): String {
    return name
        .replace("Warszawa ", "W-wa ")
        .replace("Wrocław ", "Wro ")
        .replace("Kraków ", "Kra ")
        .replace("Gdańsk ", "Gda ")
        .replace("Poznań ", "Poz ")
        .replace("Główny", "Gł.")
        .replace("Główna", "Gł.")
        .replace("Centralna", "Centr.")
        .replace("Zachodnia", "Zach.")
        .replace("Wschodnia", "Wsch.")
        .replace("Trybunalski", "Tryb.")
        .trim()
}

private fun specialSeatIcon(tags: List<String>): ImageVector? = when {
    GrmParser.TAG_WHEELCHAIR in tags -> Icons.Default.Accessible
    GrmParser.TAG_PRM_ASSIST in tags -> Icons.Default.AccessibilityNew
    GrmParser.TAG_ELDERLY in tags -> Icons.Default.Elderly
    GrmParser.TAG_PREGNANT in tags -> Icons.Default.PregnantWoman
    GrmParser.TAG_CHILD in tags -> Icons.Default.ChildFriendly
    GrmParser.TAG_BIKE in tags -> Icons.AutoMirrored.Filled.DirectionsBike
    else -> null
}

@Composable
private fun SeatAvailabilityGridDialog(
    map: FullTrainAvailabilityMap,
    seatFilters: SeatFilterPrefs,
    onDismiss: () -> Unit
) {
    val colors = LocalRailColors.current
    val matrix = remember(map) { SeatOptimizer.availabilityMapToMatrix(map) }
    val allCarriages = remember(matrix) {
        matrix.seatsMeta.values
            .map { it.carriageNo }
            .distinct()
            .sorted()
    }
    var selectedCarriage by remember(allCarriages) { mutableStateOf<Int?>(null) }
    var selectedSegIdx by remember(map) { mutableStateOf<Int?>(null) }
    var selectedSeatInfo by remember(map) { mutableStateOf<String?>(null) }

    // Show ALL scanned seats (including special seats in orange with icons so user sees full train availability)
    val filteredSeats = remember(matrix, selectedCarriage) {
        matrix.availabilityMatrix.entries
            .mapNotNull { (uid, vec) ->
                val meta = matrix.seatsMeta[uid] ?: return@mapNotNull null
                if (selectedCarriage != null && meta.carriageNo != selectedCarriage) return@mapNotNull null
                if (vec.none { it == 1 }) return@mapNotNull null
                meta to vec
            }
    }

    val segments = map.segments
    val anyScrapeError = remember(segments) { segments.any { it.status == "SCRAPE_ERROR" } }
    val hScroll = rememberScrollState()
    val seatColWidth = 78.dp
    val cellColWidth = 28.dp

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Surface(
            color = colors.card,
            shape = RoundedCornerShape(22.dp),
            border = BorderStroke(1.dp, colors.hairline),
            modifier = Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.88f)
                .padding(horizontal = 12.dp, vertical = 20.dp)
        ) {
            Column(modifier = Modifier.fillMaxSize()) {
                // Header
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(start = 16.dp, end = 6.dp, top = 10.dp, bottom = 6.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "Siatka wolnych miejsc",
                            fontWeight = FontWeight.Bold,
                            fontSize = 16.sp,
                            color = colors.textPrimary
                        )
                        Text(
                            text = "${map.trainNumber} · ${filteredSeats.size} miejsc · ${segments.size} odc.",
                            fontSize = 12.sp,
                            color = colors.textSecondary
                        )
                    }
                    IconButton(onClick = onDismiss) {
                        Icon(
                            imageVector = Icons.Default.Close,
                            contentDescription = "Zamknij siatkę",
                            tint = colors.textSecondary
                        )
                    }
                }

                // Carriage filter chips
                if (allCarriages.size > 1) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState())
                            .padding(horizontal = 14.dp, vertical = 2.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        FilterChip(
                            selected = selectedCarriage == null,
                            onClick = { selectedCarriage = null },
                            label = { Text("Wszystkie", fontSize = 12.sp) },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = colors.brandNavy,
                                selectedLabelColor = Color.White
                            )
                        )
                        allCarriages.forEach { wNo ->
                            FilterChip(
                                selected = selectedCarriage == wNo,
                                onClick = { selectedCarriage = if (selectedCarriage == wNo) null else wNo },
                                label = { Text("Wag. $wNo", fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = colors.brandNavy,
                                    selectedLabelColor = Color.White
                                )
                            )
                        }
                    }
                }

                // Compact legend
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                        Box(
                            modifier = Modifier
                                .size(12.dp)
                                .clip(RoundedCornerShape(3.dp))
                                .background(colors.seatEmerald)
                        )
                        Text("Wolne", fontSize = 11.sp, color = colors.textSecondary)
                    }
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                        Box(
                            modifier = Modifier
                                .size(12.dp)
                                .clip(RoundedCornerShape(3.dp))
                                .background(colors.corridorAmber)
                        )
                        Text("Specjalne", fontSize = 11.sp, color = colors.textSecondary)
                    }
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                        Box(
                            modifier = Modifier
                                .size(12.dp)
                                .clip(RoundedCornerShape(3.dp))
                                .background(colors.cardTonal)
                                .border(1.dp, colors.hairline, RoundedCornerShape(3.dp))
                        )
                        Text("Zajęte", fontSize = 11.sp, color = colors.textSecondary)
                    }
                    if (anyScrapeError) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                            Box(
                                modifier = Modifier
                                    .size(12.dp)
                                    .clip(RoundedCornerShape(3.dp))
                                    .background(colors.errorContainer)
                                    .border(1.dp, colors.errorRed.copy(alpha = 0.6f), RoundedCornerShape(3.dp))
                            )
                            Text("Błąd odczytu", fontSize = 11.sp, color = colors.errorRed)
                        }
                    }
                }

                val activeSeg = selectedSegIdx?.let { segments.getOrNull(it) }
                if (activeSeg != null || selectedSeatInfo != null) {
                    Surface(
                        color = colors.cardTonal,
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 14.dp, vertical = 3.dp)
                    ) {
                        val stripText = selectedSeatInfo
                            ?: "Odc. ${activeSeg!!.segmentIndex + 1}: ${activeSeg.fromStation} -> ${activeSeg.toStation} (${activeSeg.departureTime})"
                        Text(
                            text = stripText,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Medium,
                            color = colors.textPrimary,
                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                        )
                    }
                }

                HorizontalDivider(color = colors.hairline)

                if (filteredSeats.isEmpty()) {
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(24.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = "Brak wolnych miejsc pasujących do wybranych filtrów.",
                            fontSize = 13.sp,
                            color = colors.textSecondary
                        )
                    }
                } else {
                    // Column headers with vertical rotated station pairs
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(colors.cardTonal.copy(alpha = 0.55f))
                            .padding(start = 12.dp, end = 12.dp, top = 6.dp, bottom = 6.dp),
                        verticalAlignment = Alignment.Bottom
                    ) {
                        Box(
                            modifier = Modifier
                                .width(seatColWidth)
                                .height(92.dp),
                            contentAlignment = Alignment.BottomStart
                        ) {
                            Text(
                                text = "Wagon · M.",
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = colors.textSecondary
                            )
                        }
                        Row(modifier = Modifier.horizontalScroll(hScroll)) {
                            segments.forEachIndexed { idx, seg ->
                                val shortLabel = "${abbreviateStationForGrid(seg.fromStation)} -> ${abbreviateStationForGrid(seg.toStation)}"
                                val isErrCol = seg.status == "SCRAPE_ERROR"
                                Box(
                                    modifier = Modifier
                                        .width(cellColWidth)
                                        .height(92.dp)
                                        .clickable {
                                            selectedSegIdx = if (selectedSegIdx == idx) null else idx
                                        },
                                    contentAlignment = Alignment.BottomCenter
                                ) {
                                    Text(
                                        text = shortLabel,
                                        fontSize = 10.sp,
                                        fontWeight = if (selectedSegIdx == idx) FontWeight.Bold else FontWeight.Medium,
                                        color = when {
                                            isErrCol -> colors.errorRed
                                            selectedSegIdx == idx -> colors.accent
                                            else -> colors.textSecondary
                                        },
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                        modifier = Modifier
                                            .verticalRotated()
                                            .rotate(-90f)
                                    )
                                }
                            }
                        }
                    }

                    HorizontalDivider(color = colors.hairline)

                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(vertical = 4.dp)
                    ) {
                        items(
                            count = filteredSeats.size,
                            key = { idx -> filteredSeats[idx].first.seatUid }
                        ) { rowIdx ->
                            val (meta, vec) = filteredSeats[rowIdx]
                            val hasRestricted = meta.seatTags.any { GrmParser.isRestrictedTag(it) }
                            val seatIcon = specialSeatIcon(meta.seatTags)
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable {
                                        val tagsLabel = if (meta.seatTags.isNotEmpty()) {
                                            meta.seatTags.joinToString(", ") { GrmParser.formatSeatTagPl(it) }
                                        } else {
                                            "Miejsce zwykłe"
                                        }
                                        val info = "Wagon ${meta.carriageNo} · m. ${meta.seatNo}: $tagsLabel"
                                        selectedSeatInfo = if (selectedSeatInfo == info) null else info
                                    }
                                    .padding(horizontal = 12.dp, vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Row(
                                    modifier = Modifier.width(seatColWidth),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(3.dp)
                                ) {
                                    Text(
                                        text = "W${meta.carriageNo} · ${meta.seatNo}",
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        color = if (hasRestricted) colors.corridorAmber else colors.textPrimary,
                                        maxLines = 1
                                    )
                                    if (seatIcon != null) {
                                        Icon(
                                            imageVector = seatIcon,
                                            contentDescription = null,
                                            tint = colors.corridorAmber,
                                            modifier = Modifier.size(14.dp)
                                        )
                                    }
                                }
                                Row(modifier = Modifier.horizontalScroll(hScroll)) {
                                    segments.forEachIndexed { segIdx, seg ->
                                        val isFree = vec.getOrElse(segIdx) { 0 } == 1
                                        val isErr = seg.status == "SCRAPE_ERROR"
                                        val isSpecialFree = isFree && hasRestricted
                                        Box(
                                            modifier = Modifier.width(cellColWidth),
                                            contentAlignment = Alignment.Center
                                        ) {
                                            Box(
                                                contentAlignment = Alignment.Center,
                                                modifier = Modifier
                                                    .size(20.dp)
                                                    .clip(RoundedCornerShape(5.dp))
                                                    .background(
                                                        when {
                                                            isErr -> colors.errorContainer
                                                            isSpecialFree -> colors.corridorAmber
                                                            isFree -> colors.seatEmerald
                                                            else -> colors.cardTonal
                                                        }
                                                    )
                                                    .border(
                                                        width = 1.dp,
                                                        color = when {
                                                            isErr -> colors.errorRed.copy(alpha = 0.55f)
                                                            isSpecialFree -> colors.corridorAmber
                                                            isFree -> colors.seatEmerald
                                                            else -> colors.hairline
                                                        },
                                                        shape = RoundedCornerShape(5.dp)
                                                    )
                                            ) {
                                                if (isSpecialFree && seatIcon != null) {
                                                    Icon(
                                                        imageVector = seatIcon,
                                                        contentDescription = null,
                                                        tint = Color.White,
                                                        modifier = Modifier.size(13.dp)
                                                    )
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
