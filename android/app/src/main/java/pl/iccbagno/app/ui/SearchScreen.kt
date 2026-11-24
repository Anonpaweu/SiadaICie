package pl.iccbagno.app.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SwapVert
import androidx.compose.material.icons.filled.Train
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import pl.iccbagno.app.domain.StationInfo
import pl.iccbagno.app.domain.TrainConnection
import pl.iccbagno.app.optimizer.SeatOptimizer
import pl.iccbagno.app.service.ScanProgressState
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun SearchScreen(
    ui: SearchUiState,
    scan: ScanProgressState,
    onFromChange: (String) -> Unit,
    onToChange: (String) -> Unit,
    onSelectFrom: (StationInfo) -> Unit,
    onSelectTo: (StationInfo) -> Unit,
    onSwap: () -> Unit,
    onOpenDate: () -> Unit,
    onOpenTime: () -> Unit,
    onResetTime: () -> Unit,
    onSelectClass: (Int) -> Unit,
    onSearchTrains: () -> Unit,
    onSelectTrain: (TrainConnection) -> Unit,
    onOpenSettings: () -> Unit,
    onOpenCurrentScan: () -> Unit
) {
    val colors = LocalRailColors.current

    val visibleTrains = ui.trains.filter { train ->
        train.departure.trim().padStart(5, '0') >= ui.minDepartureTime
    }

    Scaffold(
        containerColor = colors.canvas,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = colors.canvas,
                    titleContentColor = colors.textPrimary
                ),
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Surface(
                            color = colors.accentContainer,
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier.size(36.dp)
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Icon(
                                    imageVector = Icons.Default.Train,
                                    contentDescription = null,
                                    tint = colors.accent,
                                    modifier = Modifier.size(20.dp)
                                )
                            }
                        }
                        Spacer(modifier = Modifier.width(10.dp))
                        Column {
                            Text(
                                text = "IccBagno",
                                fontWeight = FontWeight.Bold,
                                fontSize = 19.sp,
                                color = colors.textPrimary
                            )
                            Text(
                                text = "Wolne miejsca PKP Intercity",
                                fontSize = 11.sp,
                                color = colors.textSecondary
                            )
                        }
                    }
                },
                actions = {
                    IconButton(onClick = onOpenSettings) {
                        Icon(
                            imageVector = Icons.Default.Tune,
                            contentDescription = "Ustawienia",
                            tint = colors.textPrimary
                        )
                    }
                }
            )
        }
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .navigationBarsPadding()
                .imePadding(),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            if (scan.isRunning || scan.strategies.isNotEmpty()) {
                item(key = "active_banner") {
                    Surface(
                        onClick = onOpenCurrentScan,
                        color = if (scan.isRunning) colors.accentContainer else colors.seatEmeraldContainer,
                        shape = RoundedCornerShape(16.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 14.dp, vertical = 11.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = if (scan.isRunning) {
                                        "Skanowanie ${scan.trainNumber} (${scan.progress}%)"
                                    } else {
                                        "Gotowe wyniki dla ${scan.trainNumber}"
                                    },
                                    fontWeight = FontWeight.SemiBold,
                                    fontSize = 13.sp,
                                    color = if (scan.isRunning) colors.accent else colors.seatEmerald
                                )
                                Text(
                                    text = if (scan.isRunning) scan.statusMessage else "Dotknij, aby otworzyć szczegóły wariantów",
                                    fontSize = 11.sp,
                                    color = colors.textSecondary,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                            }
                            Icon(
                                imageVector = Icons.AutoMirrored.Filled.ArrowForward,
                                contentDescription = null,
                                tint = if (scan.isRunning) colors.accent else colors.seatEmerald,
                                modifier = Modifier.size(18.dp)
                            )
                        }
                    }
                }
            }

            // Unified Route Input Card
            item(key = "route_card") {
                Surface(
                    color = colors.card,
                    shape = RoundedCornerShape(24.dp),
                    shadowElevation = if (colors.isDark) 0.dp else 2.dp,
                    border = if (colors.isDark) BorderStroke(1.dp, colors.hairline) else null,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(14.dp)
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            // Vertical transit dots graphic
                            Column(
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.Center,
                                modifier = Modifier.padding(end = 12.dp)
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(10.dp)
                                        .border(2.dp, colors.brandNavy, CircleShape)
                                )
                                Box(
                                    modifier = Modifier
                                        .width(2.dp)
                                        .height(42.dp)
                                        .background(colors.hairline)
                                )
                                Box(
                                    modifier = Modifier
                                        .size(10.dp)
                                        .background(colors.accent, CircleShape)
                                )
                            }

                            // Origin & Destination borderless inputs
                            Column(modifier = Modifier.weight(1f)) {
                                StationFieldRow(
                                    label = "STACJA POCZĄTKOWA",
                                    value = ui.fromQuery,
                                    placeholder = "Skąd jedziesz?",
                                    onValueChange = onFromChange
                                )

                                AnimatedVisibility(visible = ui.fromSuggestions.isNotEmpty()) {
                                    SuggestionsList(ui.fromSuggestions, onSelectFrom)
                                }

                                HorizontalDivider(
                                    color = colors.hairline,
                                    modifier = Modifier.padding(vertical = 8.dp)
                                )

                                StationFieldRow(
                                    label = "STACJA DOCELOWA",
                                    value = ui.toQuery,
                                    placeholder = "Dokąd jedziesz?",
                                    onValueChange = onToChange
                                )

                                AnimatedVisibility(visible = ui.toSuggestions.isNotEmpty()) {
                                    SuggestionsList(ui.toSuggestions, onSelectTo)
                                }
                            }

                            Spacer(modifier = Modifier.width(8.dp))

                            Surface(
                                onClick = onSwap,
                                shape = CircleShape,
                                color = colors.cardTonal,
                                modifier = Modifier.size(42.dp)
                            ) {
                                Box(contentAlignment = Alignment.Center) {
                                    Icon(
                                        imageVector = Icons.Default.SwapVert,
                                        contentDescription = "Zamień stacje",
                                        tint = colors.brandNavy,
                                        modifier = Modifier.size(20.dp)
                                    )
                                }
                            }
                        }

                        // Date, time filter & class controls
                        FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Surface(
                                onClick = onOpenDate,
                                shape = RoundedCornerShape(12.dp),
                                color = colors.cardTonal
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.CalendarMonth,
                                        contentDescription = null,
                                        tint = colors.brandNavy,
                                        modifier = Modifier.size(16.dp)
                                    )
                                    Spacer(modifier = Modifier.width(6.dp))
                                    Text(
                                        text = formatDatePl(ui.travelDate),
                                        color = colors.textPrimary,
                                        fontWeight = FontWeight.Medium,
                                        fontSize = 13.sp
                                    )
                                }
                            }

                            val isTimeFiltered = ui.minDepartureTime != "00:00"
                            Surface(
                                onClick = onOpenTime,
                                shape = RoundedCornerShape(12.dp),
                                color = if (isTimeFiltered) colors.accentContainer else colors.cardTonal
                            ) {
                                Row(
                                    modifier = Modifier.padding(
                                        start = 12.dp,
                                        end = if (isTimeFiltered) 6.dp else 12.dp,
                                        top = 9.dp,
                                        bottom = 9.dp
                                    ),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.Schedule,
                                        contentDescription = null,
                                        tint = if (isTimeFiltered) colors.accent else colors.brandNavy,
                                        modifier = Modifier.size(16.dp)
                                    )
                                    Spacer(modifier = Modifier.width(6.dp))
                                    Text(
                                        text = if (isTimeFiltered) "Od ${ui.minDepartureTime}" else "Od 00:00",
                                        color = if (isTimeFiltered) colors.accent else colors.textPrimary,
                                        fontWeight = FontWeight.Medium,
                                        fontSize = 13.sp
                                    )
                                    if (isTimeFiltered) {
                                        Spacer(modifier = Modifier.width(4.dp))
                                        Box(
                                            modifier = Modifier
                                                .size(20.dp)
                                                .clip(CircleShape)
                                                .clickable { onResetTime() },
                                            contentAlignment = Alignment.Center
                                        ) {
                                            Icon(
                                                imageVector = Icons.Default.Close,
                                                contentDescription = "Resetuj godzinę",
                                                tint = colors.accent,
                                                modifier = Modifier.size(14.dp)
                                            )
                                        }
                                    }
                                }
                            }

                            SingleChoiceSegmentedButtonRow(
                                modifier = Modifier.height(38.dp)
                            ) {
                                listOf(2 to "Kl. 2", 1 to "Kl. 1").forEachIndexed { idx, (cls, label) ->
                                    SegmentedButton(
                                        selected = ui.ticketClass == cls,
                                        onClick = { onSelectClass(cls) },
                                        icon = {},
                                        shape = SegmentedButtonDefaults.itemShape(index = idx, count = 2),
                                        colors = SegmentedButtonDefaults.colors(
                                            activeContainerColor = colors.brandNavy,
                                            activeContentColor = MaterialTheme.colorScheme.onPrimary,
                                            inactiveContainerColor = colors.cardTonal,
                                            inactiveContentColor = colors.textSecondary,
                                            activeBorderColor = Color.Transparent,
                                            inactiveBorderColor = Color.Transparent
                                        ),
                                        label = {
                                            Text(
                                                text = label,
                                                fontSize = 12.sp,
                                                fontWeight = FontWeight.SemiBold
                                            )
                                        }
                                    )
                                }
                            }
                        }

                        Button(
                            onClick = onSearchTrains,
                            enabled = !ui.isLoadingTrains,
                            shape = RoundedCornerShape(16.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = colors.accent,
                                contentColor = Color.White
                            ),
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(50.dp)
                        ) {
                            if (ui.isLoadingTrains) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(18.dp),
                                    color = Color.White,
                                    strokeWidth = 2.dp
                                )
                                Spacer(modifier = Modifier.width(10.dp))
                                Text("Szukanie połączeń...", fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                            } else {
                                Icon(
                                    imageVector = Icons.Default.Search,
                                    contentDescription = null,
                                    modifier = Modifier.size(19.dp)
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text("Szukaj pociągów", fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                            }
                        }
                    }
                }
            }

            if (ui.trainsError != null) {
                item(key = "trains_error") {
                    Surface(
                        color = colors.errorContainer,
                        shape = RoundedCornerShape(16.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text(
                            text = ui.trainsError,
                            color = colors.errorRed,
                            fontSize = 13.sp,
                            modifier = Modifier.padding(14.dp)
                        )
                    }
                }
            }

            if (ui.trains.isNotEmpty()) {
                item(key = "trains_header") {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 4.dp, start = 4.dp, end = 4.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = if (ui.minDepartureTime != "00:00") {
                                "Połączenia (${visibleTrains.size} z ${ui.trains.size})"
                            } else {
                                "Połączenia (${ui.trains.size})"
                            },
                            fontWeight = FontWeight.Bold,
                            fontSize = 15.sp,
                            color = colors.textPrimary
                        )
                        if (ui.minDepartureTime != "00:00") {
                            Text(
                                text = "od ${ui.minDepartureTime}",
                                fontSize = 12.sp,
                                color = colors.textSecondary
                            )
                        }
                    }
                }

                if (visibleTrains.isEmpty()) {
                    item(key = "empty_filtered") {
                        Surface(
                            color = colors.card,
                            shape = RoundedCornerShape(18.dp),
                            border = BorderStroke(1.dp, colors.hairline),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Column(
                                modifier = Modifier.padding(16.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                Text(
                                    text = "Brak pociągów po godzinie ${ui.minDepartureTime}.",
                                    color = colors.textSecondary,
                                    fontSize = 14.sp
                                )
                                TextButton(onClick = onResetTime) {
                                    Text("Pokaż cały dzień (od 00:00)", color = colors.accent)
                                }
                            }
                        }
                    }
                } else {
                    items(
                        items = visibleTrains,
                        key = { "${it.trainNumber}_${it.departure}" }
                    ) { train ->
                        BoardingPassTrainCard(
                            train = train,
                            enabled = !scan.isRunning,
                            onClick = { onSelectTrain(train) }
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun StationFieldRow(
    label: String,
    value: String,
    placeholder: String,
    onValueChange: (String) -> Unit
) {
    val colors = LocalRailColors.current
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(
            text = label,
            fontSize = 10.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.6.sp,
            color = colors.textSecondary
        )
        Spacer(modifier = Modifier.height(2.dp))
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth()
        ) {
            Box(modifier = Modifier.weight(1f)) {
                if (value.isEmpty()) {
                    Text(
                        text = placeholder,
                        color = colors.textSecondary.copy(alpha = 0.7f),
                        fontSize = 16.sp
                    )
                }
                BasicTextField(
                    value = value,
                    onValueChange = onValueChange,
                    singleLine = true,
                    cursorBrush = SolidColor(colors.accent),
                    textStyle = TextStyle(
                        color = colors.textPrimary,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Medium
                    ),
                    modifier = Modifier.fillMaxWidth()
                )
            }
            if (value.isNotEmpty()) {
                Box(
                    modifier = Modifier
                        .size(28.dp)
                        .clip(CircleShape)
                        .clickable { onValueChange("") },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = "Wyczyść",
                        tint = colors.textSecondary,
                        modifier = Modifier.size(16.dp)
                    )
                }
            }
        }
    }
}

@Composable
private fun SuggestionsList(items: List<StationInfo>, onSelect: (StationInfo) -> Unit) {
    val colors = LocalRailColors.current
    Surface(
        color = colors.cardTonal,
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 6.dp)
            .heightIn(max = 180.dp)
    ) {
        Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
            items.forEachIndexed { idx, st ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { onSelect(st) }
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        imageVector = Icons.Default.LocationOn,
                        contentDescription = null,
                        tint = colors.brandNavy,
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = st.name,
                        color = colors.textPrimary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium,
                        modifier = Modifier.weight(1f)
                    )
                    if (st.isMainHub) {
                        Text(
                            text = "Główny",
                            fontSize = 10.sp,
                            color = colors.textSecondary
                        )
                    }
                }
                if (idx < items.lastIndex) {
                    HorizontalDivider(color = colors.hairline.copy(alpha = 0.6f))
                }
            }
        }
    }
}

@Composable
private fun BoardingPassTrainCard(
    train: TrainConnection,
    enabled: Boolean,
    onClick: () -> Unit
) {
    val colors = LocalRailColors.current
    val brand = train.trainNumber.trim().split(Regex("\\s+")).firstOrNull()?.uppercase().orEmpty()
    val (badgeBg, badgeFg) = when (brand) {
        "EIP" -> Color(0xFFFEF3C7) to Color(0xFF92400E)
        "EIC" -> Color(0xFFE0E7FF) to Color(0xFF3730A3)
        "TLK" -> Color(0xFFCCFBF1) to Color(0xFF115E59)
        else -> colors.cardTonal to colors.brandNavy
    }

    Surface(
        onClick = onClick,
        enabled = enabled,
        color = colors.card,
        shape = RoundedCornerShape(18.dp),
        shadowElevation = if (colors.isDark) 0.dp else 1.5.dp,
        border = if (colors.isDark) BorderStroke(1.dp, colors.hairline) else null,
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.weight(1f)
                ) {
                    Surface(color = badgeBg, shape = RoundedCornerShape(6.dp)) {
                        Text(
                            text = brand.ifEmpty { "IC" },
                            color = badgeFg,
                            fontWeight = FontWeight.Bold,
                            fontSize = 11.sp,
                            modifier = Modifier.padding(horizontal = 7.dp, vertical = 2.dp)
                        )
                    }
                    Text(
                        text = train.trainNumber,
                        color = colors.textPrimary,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 14.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }

                Text(
                    text = if (train.changes == 0) "Bezpośredni" else SeatOptimizer.formatTransfersPl(train.changes),
                    color = if (train.changes == 0) colors.seatEmerald else colors.textSecondary,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Medium
                )
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = train.departure.ifBlank { "--:--" },
                    style = TextStyle(
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Bold,
                        fontFeatureSettings = "tnum",
                        color = colors.textPrimary
                    )
                )

                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier
                        .weight(1f)
                        .padding(horizontal = 12.dp)
                ) {
                    if (train.duration.isNotBlank()) {
                        Text(
                            text = formatDurationCompact(train.duration),
                            fontSize = 11.sp,
                            color = colors.textSecondary,
                            fontWeight = FontWeight.Medium
                        )
                        Spacer(modifier = Modifier.height(3.dp))
                    }
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(1.5.dp)
                            .background(colors.hairline)
                    )
                }

                Text(
                    text = train.arrival.ifBlank { "--:--" },
                    style = TextStyle(
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Bold,
                        fontFeatureSettings = "tnum",
                        color = colors.textPrimary
                    )
                )

                Spacer(modifier = Modifier.width(12.dp))

                Surface(
                    color = colors.accentContainer,
                    shape = RoundedCornerShape(10.dp)
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "Skanuj",
                            color = colors.accent,
                            fontWeight = FontWeight.SemiBold,
                            fontSize = 12.sp
                        )
                        Spacer(modifier = Modifier.width(3.dp))
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowForward,
                            contentDescription = null,
                            tint = colors.accent,
                            modifier = Modifier.size(14.dp)
                        )
                    }
                }
            }
        }
    }
}

fun formatDatePl(isoDate: String): String {
    return try {
        val dt = LocalDate.parse(isoDate.trim(), DateTimeFormatter.ISO_LOCAL_DATE)
        val formatter = DateTimeFormatter.ofPattern("d MMM, EEE", Locale("pl", "PL"))
        dt.format(formatter)
    } catch (_: Exception) {
        isoDate
    }
}

private fun formatDurationCompact(raw: String): String {
    val parts = raw.trim().split(":")
    if (parts.size == 2) {
        val h = parts[0].toIntOrNull()
        val m = parts[1].toIntOrNull()
        if (h != null && m != null) {
            return if (h > 0) "${h}h ${m}m" else "${m} min"
        }
    }
    return raw
}
