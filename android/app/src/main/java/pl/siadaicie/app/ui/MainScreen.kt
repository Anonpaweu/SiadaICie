package pl.siadaicie.app.ui

import android.content.Context
import android.content.Intent
import android.graphics.ImageDecoder
import android.graphics.drawable.AnimatedImageDrawable
import android.net.Uri
import android.widget.ImageView
import androidx.activity.compose.BackHandler
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.runtime.LaunchedEffect
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
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.delay
import pl.siadaicie.app.R
import pl.siadaicie.app.domain.SeatFilterPrefs
import pl.siadaicie.app.domain.StationInfo
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

private const val SUPPORT_COFFEE_URL = "https://buycoffee.to/anonpaweu"

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
    var showFavoritesMenu by remember { mutableStateOf(false) }

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
                onExpandTrainStops = viewModel::loadTrainRouteStops,
                onSelectStartStop = viewModel::toggleSelectedStartStop,
                onOpenSettings = { showSettings = true },
                onOpenSupport = { openUrlSafe(context, SUPPORT_COFFEE_URL) },
                onOpenCurrentScan = viewModel::openScanScreen,
                onOpenHistoryItem = viewModel::openHistoryItem,
                onRescanHistoryItem = viewModel::rescanHistoryItem,
                onRefreshHistory = viewModel::refreshHistory,
                isFavoritesOpen = showFavoritesMenu,
                onToggleFavorites = { showFavoritesMenu = !showFavoritesMenu },
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
                onOpenBooking = { url -> openUrlSafe(context, url) },
                onOpenSupport = { openUrlSafe(context, SUPPORT_COFFEE_URL) }
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

    if (showFavoritesMenu) {
        FavoritesMenuDialog(
            favorites = ui.favorites,
            onApplyFavorite = { fav ->
                viewModel.applyFavorite(fav)
                showFavoritesMenu = false
            },
            onAddFavorite = viewModel::addFavorite,
            onUpdateFavorite = viewModel::updateFavorite,
            onRemoveFavorite = viewModel::removeFavorite,
            onSearchStations = viewModel::searchStationsForFavorites,
            onDismiss = { showFavoritesMenu = false }
        )
    }
}

@Composable
private fun FavoritesMenuDialog(
    favorites: List<FavoriteRoute>,
    onApplyFavorite: (FavoriteRoute) -> Unit,
    onAddFavorite: (String, String) -> String?,
    onUpdateFavorite: (Int, String, String) -> String?,
    onRemoveFavorite: (FavoriteRoute) -> Unit,
    onSearchStations: suspend (String) -> List<StationInfo>,
    onDismiss: () -> Unit
) {
    val colors = LocalRailColors.current
    val focusManager = LocalFocusManager.current
    val keyboardController = LocalSoftwareKeyboardController.current

    var showForm by remember { mutableStateOf(false) }
    var editingIndex by remember { mutableStateOf<Int?>(null) }
    var fromInput by remember { mutableStateOf("") }
    var toInput by remember { mutableStateOf("") }
    var activeField by remember { mutableStateOf<String?>(null) }
    var fromSuggestions by remember { mutableStateOf<List<StationInfo>>(emptyList()) }
    var toSuggestions by remember { mutableStateOf<List<StationInfo>>(emptyList()) }
    var formError by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(showForm, fromInput, activeField) {
        if (!showForm || activeField != "from" || fromInput.trim().length < 2) {
            fromSuggestions = emptyList()
            return@LaunchedEffect
        }
        delay(150)
        fromSuggestions = onSearchStations(fromInput)
    }

    LaunchedEffect(showForm, toInput, activeField) {
        if (!showForm || activeField != "to" || toInput.trim().length < 2) {
            toSuggestions = emptyList()
            return@LaunchedEffect
        }
        delay(150)
        toSuggestions = onSearchStations(toInput)
    }

    fun closeForm() {
        focusManager.clearFocus()
        keyboardController?.hide()
        showForm = false
        editingIndex = null
        fromInput = ""
        toInput = ""
        activeField = null
        fromSuggestions = emptyList()
        toSuggestions = emptyList()
        formError = null
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null
                ) {
                    focusManager.clearFocus()
                    keyboardController?.hide()
                    onDismiss()
                }
                .padding(horizontal = 16.dp, vertical = 24.dp)
                .imePadding(),
            contentAlignment = Alignment.Center
        ) {
            Surface(
                color = colors.card,
                shape = RoundedCornerShape(24.dp),
                border = BorderStroke(1.dp, colors.hairline),
                shadowElevation = 10.dp,
                modifier = Modifier
                    .fillMaxWidth()
                    .widthIn(max = 460.dp)
                    .heightIn(max = 560.dp)
                    .clickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null
                    ) {}
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(18.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp)
                        ) {
                            Surface(
                                onClick = onDismiss,
                                shape = CircleShape,
                                color = colors.accentContainer,
                                modifier = Modifier.size(34.dp)
                            ) {
                                Box(contentAlignment = Alignment.Center) {
                                    Icon(
                                        imageVector = Icons.Default.Star,
                                        contentDescription = "Zamknij ulubione",
                                        tint = colors.accent,
                                        modifier = Modifier.size(18.dp)
                                    )
                                }
                            }
                            Column {
                                Text(
                                    text = "Ulubione połączenia",
                                    fontSize = 17.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = colors.textPrimary
                                )
                                Text(
                                    text = "Zapisano ${favorites.size}/4 tras",
                                    fontSize = 11.sp,
                                    color = colors.textSecondary
                                )
                            }
                        }
                        IconButton(
                            onClick = onDismiss,
                            modifier = Modifier.size(34.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Default.Close,
                                contentDescription = "Zamknij",
                                tint = colors.textSecondary,
                                modifier = Modifier.size(20.dp)
                            )
                        }
                    }

                    HorizontalDivider(color = colors.hairline)

                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        if (favorites.isEmpty() && !showForm) {
                            Text(
                                text = "Brak zapisanych tras. Dodaj ulubione połączenie, aby szybko wybierać je jednym dotknięciem.",
                                fontSize = 13.sp,
                                color = colors.textSecondary,
                                lineHeight = 18.sp
                            )
                        }

                        favorites.forEachIndexed { idx, fav ->
                            Surface(
                                onClick = {
                                    focusManager.clearFocus()
                                    keyboardController?.hide()
                                    onApplyFavorite(fav)
                                },
                                color = colors.cardTonal,
                                shape = RoundedCornerShape(14.dp),
                                border = BorderStroke(
                                    width = 1.dp,
                                    color = if (editingIndex == idx && showForm) colors.accent else colors.hairline
                                ),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(start = 12.dp, end = 4.dp, top = 8.dp, bottom = 8.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Row(
                                        modifier = Modifier.weight(1f),
                                        verticalAlignment = Alignment.CenterVertically,
                                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                                    ) {
                                        Text(
                                            text = fav.fromStation,
                                            fontSize = 13.sp,
                                            fontWeight = FontWeight.SemiBold,
                                            color = colors.textPrimary,
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis,
                                            modifier = Modifier.weight(1f, fill = false)
                                        )
                                        Icon(
                                            imageVector = Icons.AutoMirrored.Filled.ArrowForward,
                                            contentDescription = null,
                                            tint = colors.accent,
                                            modifier = Modifier.size(14.dp)
                                        )
                                        Text(
                                            text = fav.toStation,
                                            fontSize = 13.sp,
                                            fontWeight = FontWeight.SemiBold,
                                            color = colors.textPrimary,
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis,
                                            modifier = Modifier.weight(1f, fill = false)
                                        )
                                    }
                                    IconButton(
                                        onClick = {
                                            editingIndex = idx
                                            fromInput = fav.fromStation
                                            toInput = fav.toStation
                                            activeField = null
                                            fromSuggestions = emptyList()
                                            toSuggestions = emptyList()
                                            formError = null
                                            showForm = true
                                        },
                                        modifier = Modifier.size(34.dp)
                                    ) {
                                        Icon(
                                            imageVector = Icons.Default.Edit,
                                            contentDescription = "Edytuj połączenie",
                                            tint = colors.brandNavy,
                                            modifier = Modifier.size(17.dp)
                                        )
                                    }
                                    IconButton(
                                        onClick = {
                                            if (editingIndex == idx) {
                                                closeForm()
                                            }
                                            onRemoveFavorite(fav)
                                        },
                                        modifier = Modifier.size(34.dp)
                                    ) {
                                        Icon(
                                            imageVector = Icons.Default.Delete,
                                            contentDescription = "Usuń połączenie",
                                            tint = colors.textSecondary,
                                            modifier = Modifier.size(17.dp)
                                        )
                                    }
                                }
                            }
                        }

                        AnimatedVisibility(visible = showForm) {
                            Surface(
                                color = colors.cardTonal,
                                shape = RoundedCornerShape(16.dp),
                                border = BorderStroke(1.dp, colors.accent.copy(alpha = 0.4f)),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Column(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(14.dp),
                                    verticalArrangement = Arrangement.spacedBy(10.dp)
                                ) {
                                    Text(
                                        text = if (editingIndex != null) "Edytuj ulubione połączenie" else "Nowe ulubione połączenie",
                                        fontSize = 13.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = colors.textPrimary
                                    )

                                    StationFieldRow(
                                        label = "STACJA POCZĄTKOWA",
                                        value = fromInput,
                                        placeholder = "Skąd?",
                                        onValueChange = {
                                            fromInput = it
                                            activeField = "from"
                                            toSuggestions = emptyList()
                                            formError = null
                                        }
                                    )

                                    AnimatedVisibility(visible = fromSuggestions.isNotEmpty()) {
                                        SuggestionsList(fromSuggestions) { st ->
                                            fromInput = st.name
                                            activeField = null
                                            fromSuggestions = emptyList()
                                            formError = null
                                            focusManager.clearFocus()
                                            keyboardController?.hide()
                                        }
                                    }

                                    HorizontalDivider(color = colors.hairline)

                                    StationFieldRow(
                                        label = "STACJA DOCELOWA",
                                        value = toInput,
                                        placeholder = "Dokąd?",
                                        onValueChange = {
                                            toInput = it
                                            activeField = "to"
                                            fromSuggestions = emptyList()
                                            formError = null
                                        }
                                    )

                                    AnimatedVisibility(visible = toSuggestions.isNotEmpty()) {
                                        SuggestionsList(toSuggestions) { st ->
                                            toInput = st.name
                                            activeField = null
                                            toSuggestions = emptyList()
                                            formError = null
                                            focusManager.clearFocus()
                                            keyboardController?.hide()
                                        }
                                    }

                                    if (formError != null) {
                                        Text(
                                            text = formError.orEmpty(),
                                            fontSize = 12.sp,
                                            color = colors.corridorAmber
                                        )
                                    }

                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                                    ) {
                                        OutlinedButton(
                                            onClick = { closeForm() },
                                            shape = RoundedCornerShape(12.dp),
                                            border = BorderStroke(1.dp, colors.hairline),
                                            modifier = Modifier.weight(1f)
                                        ) {
                                            Text("Anuluj", color = colors.textSecondary, fontSize = 13.sp)
                                        }
                                        Button(
                                            onClick = {
                                                focusManager.clearFocus()
                                                keyboardController?.hide()
                                                val err = if (editingIndex != null) {
                                                    onUpdateFavorite(editingIndex!!, fromInput, toInput)
                                                } else {
                                                    onAddFavorite(fromInput, toInput)
                                                }
                                                if (err == null) {
                                                    closeForm()
                                                } else {
                                                    formError = err
                                                }
                                            },
                                            shape = RoundedCornerShape(12.dp),
                                            colors = ButtonDefaults.buttonColors(
                                                containerColor = colors.accent,
                                                contentColor = Color.White
                                            ),
                                            modifier = Modifier.weight(1f)
                                        ) {
                                            Text("Zapisz", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                                        }
                                    }
                                }
                            }
                        }

                        if (!showForm && favorites.size < 4) {
                            OutlinedButton(
                                onClick = {
                                    editingIndex = null
                                    fromInput = ""
                                    toInput = ""
                                    activeField = null
                                    fromSuggestions = emptyList()
                                    toSuggestions = emptyList()
                                    formError = null
                                    showForm = true
                                },
                                shape = RoundedCornerShape(14.dp),
                                border = BorderStroke(1.dp, colors.accent.copy(alpha = 0.6f)),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(44.dp)
                            ) {
                                Icon(
                                    imageVector = Icons.Default.Add,
                                    contentDescription = null,
                                    tint = colors.accent,
                                    modifier = Modifier.size(18.dp)
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    text = "Dodaj ulubione połączenie",
                                    color = colors.accent,
                                    fontWeight = FontWeight.SemiBold,
                                    fontSize = 13.sp
                                )
                            }
                        }
                    }
                }
            }
        }
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
