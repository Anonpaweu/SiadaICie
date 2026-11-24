package pl.iccbagno.app.ui

import android.content.Context
import android.content.Intent
import android.graphics.ImageDecoder
import android.graphics.drawable.AnimatedImageDrawable
import android.net.Uri
import android.widget.ImageView
import androidx.activity.compose.BackHandler
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import pl.iccbagno.app.R
import pl.iccbagno.app.domain.SeatFilterPrefs
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(viewModel: MainViewModel) {
    val ui by viewModel.uiState.collectAsState()
    val scan by viewModel.scanState.collectAsState()
    val history by viewModel.history.collectAsState()
    val context = LocalContext.current
    val colors = LocalRailColors.current

    var showSettings by remember { mutableStateOf(false) }
    var showDatePicker by remember { mutableStateOf(false) }
    var showTimePicker by remember { mutableStateOf(false) }
    var showWheelchairConfirm by remember { mutableStateOf(false) }
    var showClearHistoryConfirm by remember { mutableStateOf(false) }

    val handleSeatFilterChange: (SeatFilterPrefs) -> Unit = { next ->
        if (!ui.seatFilters.allowWheelchair && next.allowWheelchair) {
            showWheelchairConfirm = true
        } else {
            viewModel.updateSeatFilters(next)
        }
    }

    BackHandler(enabled = ui.activeScreen == AppScreen.SCAN_RESULT) {
        viewModel.navigateBackToSearch()
    }

    AnimatedContent(
        targetState = ui.activeScreen,
        transitionSpec = { fadeIn() togetherWith fadeOut() },
        label = "screen_nav"
    ) { screen ->
        when (screen) {
            AppScreen.SEARCH -> SearchScreen(
                ui = ui,
                scan = scan,
                history = history,
                onFromChange = viewModel::onFromQueryChange,
                onToChange = viewModel::onToQueryChange,
                onSelectFrom = viewModel::selectFromStation,
                onSelectTo = viewModel::selectToStation,
                onSwap = viewModel::swapStations,
                onOpenDate = { showDatePicker = true },
                onOpenTime = { showTimePicker = true },
                onResetTime = { viewModel.setMinDepartureTime("00:00") },
                onSelectClass = viewModel::setTicketClass,
                onDirectOnlyChange = viewModel::setDirectOnly,
                onSeatFiltersChange = handleSeatFilterChange,
                onSearchTrains = viewModel::searchTrains,
                onSelectTrain = viewModel::startTrainScan,
                onOpenSettings = { showSettings = true },
                onOpenCurrentScan = viewModel::openScanScreen,
                onOpenHistoryItem = viewModel::openHistoryItem,
                onClearHistory = { showClearHistoryConfirm = true }
            )

            AppScreen.SCAN_RESULT -> ResultScreen(
                ui = ui,
                scan = scan,
                onBack = viewModel::navigateBackToSearch,
                onCancelScan = viewModel::cancelScan,
                onRetryScan = viewModel::retryLastScan,
                onProfileChange = viewModel::setProfile,
                onSeatFiltersChange = handleSeatFilterChange,
                onSelectStrategy = viewModel::selectStrategy,
                onOpenBooking = { url -> openUrlSafe(context, url) }
            )
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
                }) { Text("Wybierz", color = colors.accent) }
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
            containerColor = colors.card,
            title = { Text("Od godziny", color = colors.textPrimary, fontWeight = FontWeight.Bold) },
            text = { TimePicker(state = timeState) },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.setMinDepartureTime("%02d:%02d".format(timeState.hour, timeState.minute))
                    showTimePicker = false
                }) { Text("Wybierz", color = colors.accent) }
            },
            dismissButton = {
                TextButton(onClick = { showTimePicker = false }) { Text("Anuluj") }
            }
        )
    }

    if (showSettings) {
        val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ModalBottomSheet(
            onDismissRequest = { showSettings = false },
            sheetState = sheetState,
            containerColor = colors.card
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .padding(horizontal = 20.dp)
                    .padding(bottom = 24.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp)
            ) {
                Text(
                    text = "Ustawienia",
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                    color = colors.textPrimary
                )

                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(
                        text = "Motyw aplikacji",
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = colors.textSecondary
                    )
                    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                        ThemeMode.entries.forEachIndexed { idx, mode ->
                            SegmentedButton(
                                selected = ui.themeMode == mode,
                                onClick = { viewModel.setThemeMode(mode) },
                                icon = {},
                                shape = SegmentedButtonDefaults.itemShape(
                                    index = idx,
                                    count = ThemeMode.entries.size
                                ),
                                colors = SegmentedButtonDefaults.colors(
                                    activeContainerColor = colors.brandNavy,
                                    activeContentColor = MaterialTheme.colorScheme.onPrimary,
                                    inactiveContainerColor = colors.cardTonal,
                                    inactiveContentColor = colors.textSecondary,
                                    activeBorderColor = Color.Transparent,
                                    inactiveBorderColor = Color.Transparent
                                ),
                                label = { Text(mode.label, fontSize = 12.sp) }
                            )
                        }
                    }
                }

                HorizontalDivider(color = colors.hairline)

                SettingsToggleRow(
                    title = "Pomiń Fast-Path",
                    subtitle = "Zawsze skanuj każdy odcinek po kolei",
                    checked = ui.forceFullScan,
                    onCheckedChange = viewModel::setForceFullScan
                )

                HorizontalDivider(color = colors.hairline)

                SettingsToggleRow(
                    title = "Podgląd WebView (diagnostyka)",
                    subtitle = "Wyświetla ukryte okno ebilet.intercity.pl podczas skanu",
                    checked = ui.debugWebViewEnabled,
                    onCheckedChange = viewModel::setDebugWebViewEnabled
                )

                HorizontalDivider(color = colors.hairline)

                Text(
                    text = "Prywatność i bezpieczeństwo: komunikacja wyłącznie przez HTTPS, bez telemetryki i haseł w aplikacji. Zakup biletu otwiera się w systemowej przeglądarce e-IC.",
                    fontSize = 11.sp,
                    color = colors.textSecondary
                )
                Spacer(modifier = Modifier.height(4.dp))
            }
        }
    }

    if (showWheelchairConfirm) {
        WheelchairConfirmDialog(
            onConfirm = {
                viewModel.updateSeatFilters(ui.seatFilters.copy(allowWheelchair = true))
                showWheelchairConfirm = false
            },
            onDismiss = {
                showWheelchairConfirm = false
            }
        )
    }

    if (showClearHistoryConfirm) {
        AlertDialog(
            onDismissRequest = { showClearHistoryConfirm = false },
            containerColor = colors.card,
            title = {
                Text(
                    text = "Wyczyść historię",
                    color = colors.textPrimary,
                    fontWeight = FontWeight.Bold
                )
            },
            text = {
                Text(
                    text = "Czy na pewno chcesz wyczyścić historię ostatnich analiz?",
                    color = colors.textSecondary
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.clearHistory()
                    showClearHistoryConfirm = false
                }) {
                    Text("Tak", color = colors.accent)
                }
            },
            dismissButton = {
                TextButton(onClick = { showClearHistoryConfirm = false }) {
                    Text("Nie")
                }
            }
        )
    }
}

@Composable
private fun WheelchairConfirmDialog(
    onConfirm: () -> Unit,
    onDismiss: () -> Unit
) {
    val colors = LocalRailColors.current
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Surface(
            color = colors.card,
            shape = RoundedCornerShape(24.dp),
            border = BorderStroke(1.5.dp, colors.hairline),
            shadowElevation = 10.dp,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 14.dp)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(18.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                AndroidView(
                    factory = { ctx ->
                        ImageView(ctx).apply {
                            scaleType = ImageView.ScaleType.FIT_CENTER
                            adjustViewBounds = true
                            try {
                                val src = ImageDecoder.createSource(ctx.resources, R.raw.the_rock_rock)
                                val drawable = ImageDecoder.decodeDrawable(src)
                                setImageDrawable(drawable)
                                (drawable as? AnimatedImageDrawable)?.start()
                            } catch (_: Exception) {
                            }
                        }
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(1.22f)
                        .clip(RoundedCornerShape(16.dp))
                )

                Text(
                    text = "Na pewno jesteś niepełnosprawny?",
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    color = colors.textPrimary,
                    textAlign = TextAlign.Center
                )

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    OutlinedButton(
                        onClick = onDismiss,
                        shape = RoundedCornerShape(14.dp),
                        border = BorderStroke(1.dp, colors.hairline),
                        modifier = Modifier
                            .weight(1f)
                            .height(48.dp)
                    ) {
                        Text(
                            text = "Nie",
                            color = colors.textPrimary,
                            fontWeight = FontWeight.SemiBold,
                            fontSize = 15.sp
                        )
                    }
                    Button(
                        onClick = onConfirm,
                        shape = RoundedCornerShape(14.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = colors.accent,
                            contentColor = Color.White
                        ),
                        modifier = Modifier
                            .weight(1f)
                            .height(48.dp)
                    ) {
                        Text(
                            text = "Tak",
                            fontWeight = FontWeight.Bold,
                            fontSize = 15.sp
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun SettingsToggleRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit
) {
    val colors = LocalRailColors.current
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f).padding(end = 12.dp)) {
            Text(title, color = colors.textPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(subtitle, color = colors.textSecondary, fontSize = 12.sp)
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

private fun openUrlSafe(context: Context, url: String) {
    try {
        CustomTabsIntent.Builder()
            .setShowTitle(true)
            .build()
            .launchUrl(context, Uri.parse(url))
    } catch (_: Exception) {
        try {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: Exception) {
        }
    }
}
