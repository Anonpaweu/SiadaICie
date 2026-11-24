package pl.iccbagno.app.ui

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import pl.iccbagno.app.data.ScanHistoryItem
import pl.iccbagno.app.data.ScanHistoryStore
import pl.iccbagno.app.data.StationsRepository
import pl.iccbagno.app.domain.SeatFilterPrefs
import pl.iccbagno.app.domain.StationInfo
import pl.iccbagno.app.domain.TrainConnection
import pl.iccbagno.app.optimizer.OptimizationProfile
import pl.iccbagno.app.scraper.ScheduleClient
import pl.iccbagno.app.service.ScanForegroundService
import pl.iccbagno.app.service.ScanProgressState
import java.time.LocalDate
import java.time.format.DateTimeFormatter

enum class AppScreen {
    SEARCH,
    SCAN_RESULT
}

data class SearchUiState(
    val activeScreen: AppScreen = AppScreen.SEARCH,
    val themeMode: ThemeMode = ThemeMode.SYSTEM,
    val fromQuery: String = "Warszawa Centralna",
    val toQuery: String = "Kraków Główny",
    val fromSuggestions: List<StationInfo> = emptyList(),
    val toSuggestions: List<StationInfo> = emptyList(),
    val travelDate: String = LocalDate.now().plusDays(1).format(DateTimeFormatter.ISO_LOCAL_DATE),
    val minDepartureTime: String = "00:00",
    val ticketClass: Int = 2,
    val profile: OptimizationProfile = OptimizationProfile.BALANCED,
    val directOnly: Boolean = true,
    val seatFilters: SeatFilterPrefs = SeatFilterPrefs(),
    val forceFullScan: Boolean = false,
    val debugWebViewEnabled: Boolean = false,
    val isLoadingTrains: Boolean = false,
    val trainsError: String? = null,
    val trains: List<TrainConnection> = emptyList(),
    val selectedStrategyIdx: Int = 0,
    val lastScannedTrain: TrainConnection? = null
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val prefs = application.applicationContext.getSharedPreferences(
        "ic_seat_optimizer_prefs",
        Context.MODE_PRIVATE
    )
    private val stationsRepo = StationsRepository(application.applicationContext)
    private val scheduleClient = ScheduleClient(stationsRepo)

    private val _uiState = MutableStateFlow(loadInitialUiState())
    val uiState: StateFlow<SearchUiState> = _uiState.asStateFlow()

    val scanState: StateFlow<ScanProgressState> = ScanForegroundService.state
    val history: StateFlow<List<ScanHistoryItem>> = ScanHistoryStore.history

    init {
        ScanHistoryStore.refresh(application.applicationContext)
    }

    private fun loadInitialUiState(): SearchUiState {
        return SearchUiState(
            directOnly = prefs.getBoolean("direct_only", true),
            seatFilters = SeatFilterPrefs(
                allowBike = prefs.getBoolean("allow_bike", true),
                allowPregnant = prefs.getBoolean("allow_pregnant", false),
                allowChild = prefs.getBoolean("allow_child", false),
                allowElderly = prefs.getBoolean("allow_elderly", false),
                allowPrmAssist = prefs.getBoolean("allow_prm_assist", false),
                allowWheelchair = prefs.getBoolean("allow_wheelchair", false)
            )
        )
    }

    private fun saveFilterPrefs(directOnly: Boolean, filters: SeatFilterPrefs) {
        prefs.edit()
            .putBoolean("direct_only", directOnly)
            .putBoolean("allow_bike", filters.allowBike)
            .putBoolean("allow_pregnant", filters.allowPregnant)
            .putBoolean("allow_child", filters.allowChild)
            .putBoolean("allow_elderly", filters.allowElderly)
            .putBoolean("allow_prm_assist", filters.allowPrmAssist)
            .putBoolean("allow_wheelchair", filters.allowWheelchair)
            .apply()
    }

    private var fromSearchJob: Job? = null
    private var toSearchJob: Job? = null

    fun onFromQueryChange(q: String) {
        _uiState.update { it.copy(fromQuery = q, toSuggestions = emptyList()) }
        fromSearchJob?.cancel()
        if (q.trim().length < 2) {
            _uiState.update { it.copy(fromSuggestions = emptyList()) }
            return
        }
        fromSearchJob = viewModelScope.launch(Dispatchers.IO) {
            delay(150)
            val list = stationsRepo.searchStations(q, 10)
            _uiState.update { it.copy(fromSuggestions = list) }
        }
    }

    fun onToQueryChange(q: String) {
        _uiState.update { it.copy(toQuery = q, fromSuggestions = emptyList()) }
        toSearchJob?.cancel()
        if (q.trim().length < 2) {
            _uiState.update { it.copy(toSuggestions = emptyList()) }
            return
        }
        toSearchJob = viewModelScope.launch(Dispatchers.IO) {
            delay(150)
            val list = stationsRepo.searchStations(q, 10)
            _uiState.update { it.copy(toSuggestions = list) }
        }
    }

    fun selectFromStation(st: StationInfo) {
        _uiState.update { it.copy(fromQuery = st.name, fromSuggestions = emptyList()) }
    }

    fun selectToStation(st: StationInfo) {
        _uiState.update { it.copy(toQuery = st.name, toSuggestions = emptyList()) }
    }

    fun dismissSuggestions() {
        _uiState.update { it.copy(fromSuggestions = emptyList(), toSuggestions = emptyList()) }
    }

    fun swapStations() {
        _uiState.update {
            it.copy(
                fromQuery = it.toQuery,
                toQuery = it.fromQuery,
                fromSuggestions = emptyList(),
                toSuggestions = emptyList()
            )
        }
    }

    fun setTravelDate(isoDate: String) {
        _uiState.update { it.copy(travelDate = isoDate) }
    }

    fun setMinDepartureTime(hhMm: String) {
        _uiState.update { it.copy(minDepartureTime = hhMm) }
    }

    fun setTicketClass(cls: Int) {
        _uiState.update { it.copy(ticketClass = cls) }
    }

    fun setThemeMode(mode: ThemeMode) {
        _uiState.update { it.copy(themeMode = mode) }
    }

    fun setProfile(profile: OptimizationProfile) {
        val filters = _uiState.value.seatFilters
        _uiState.update { it.copy(profile = profile, selectedStrategyIdx = 0) }
        viewModelScope.launch(Dispatchers.Default) {
            ScanForegroundService.recomputeStrategies(profile, filters)
        }
    }

    fun setDirectOnly(directOnly: Boolean) {
        val filters = _uiState.value.seatFilters
        _uiState.update { it.copy(directOnly = directOnly) }
        saveFilterPrefs(directOnly, filters)
    }

    fun updateSeatFilters(filters: SeatFilterPrefs) {
        val st = _uiState.value
        _uiState.update { it.copy(seatFilters = filters, selectedStrategyIdx = 0) }
        saveFilterPrefs(st.directOnly, filters)
        viewModelScope.launch(Dispatchers.Default) {
            ScanForegroundService.recomputeStrategies(st.profile, filters)
        }
    }

    fun setForceFullScan(forceFull: Boolean) {
        _uiState.update { it.copy(forceFullScan = forceFull) }
    }

    fun setDebugWebViewEnabled(enabled: Boolean) {
        _uiState.update { it.copy(debugWebViewEnabled = enabled) }
    }

    fun selectStrategy(idx: Int) {
        _uiState.update { it.copy(selectedStrategyIdx = idx) }
    }

    fun navigateBackToSearch() {
        _uiState.update { it.copy(activeScreen = AppScreen.SEARCH) }
    }

    fun openScanScreen() {
        _uiState.update { it.copy(activeScreen = AppScreen.SCAN_RESULT) }
    }

    fun openHistoryItem(item: ScanHistoryItem) {
        val st = _uiState.value
        ScanForegroundService.loadFromHistory(item, st.profile, st.seatFilters)
        _uiState.update {
            it.copy(
                selectedStrategyIdx = 0,
                activeScreen = AppScreen.SCAN_RESULT,
                fromQuery = item.fromStation,
                toQuery = item.toStation,
                travelDate = item.travelDate,
                ticketClass = item.ticketClass
            )
        }
    }

    fun clearHistory() {
        ScanHistoryStore.clear(getApplication<Application>().applicationContext)
    }

    fun searchTrains() {
        val st = _uiState.value
        val from = st.fromQuery.trim()
        val to = st.toQuery.trim()
        if (from.isEmpty() || to.isEmpty()) {
            _uiState.update { it.copy(trainsError = "Podaj stację początkową i docelową.") }
            return
        }
        dismissSuggestions()
        _uiState.update { it.copy(isLoadingTrains = true, trainsError = null, trains = emptyList()) }

        viewModelScope.launch {
            try {
                val res = scheduleClient.fetchLiveTrainConnections(
                    fromStation = from,
                    toStation = to,
                    travelDate = st.travelDate,
                    directOnly = st.directOnly
                )
                _uiState.update {
                    it.copy(
                        isLoadingTrains = false,
                        trains = res,
                        trainsError = if (res.isEmpty()) "Nie znaleziono połączeń PKP Intercity dla tej trasy." else null
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(isLoadingTrains = false, trainsError = "Błąd pobierania rozkładu: ${e.message}")
                }
            }
        }
    }

    fun startTrainScan(train: TrainConnection) {
        val st = _uiState.value
        _uiState.update {
            it.copy(
                selectedStrategyIdx = 0,
                activeScreen = AppScreen.SCAN_RESULT,
                lastScannedTrain = train
            )
        }
        ScanForegroundService.startScan(
            context = getApplication<Application>().applicationContext,
            fromStation = st.fromQuery.trim(),
            toStation = st.toQuery.trim(),
            travelDate = st.travelDate,
            trainNumber = train.trainNumber,
            depTime = train.departure,
            ticketClass = st.ticketClass,
            profile = st.profile,
            forceFull = st.forceFullScan,
            seatFilters = st.seatFilters
        )
    }

    fun retryLastScan() {
        val last = _uiState.value.lastScannedTrain ?: return
        startTrainScan(last)
    }

    fun cancelScan() {
        ScanForegroundService.cancelScan(getApplication<Application>().applicationContext)
    }
}
