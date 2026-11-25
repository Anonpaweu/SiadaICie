package pl.iccbagno.app.ui

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
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

data class FavoriteRoute(
    val fromStation: String,
    val toStation: String
)

data class SearchUiState(
    val activeScreen: AppScreen = AppScreen.SEARCH,
    val themeMode: ThemeMode = ThemeMode.SYSTEM,
    val fromQuery: String = "",
    val toQuery: String = "",
    val fromSuggestions: List<StationInfo> = emptyList(),
    val toSuggestions: List<StationInfo> = emptyList(),
    val travelDate: String = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE),
    val minDepartureTime: String = "00:00",
    val ticketClass: Int = 2,
    val profile: OptimizationProfile = OptimizationProfile.BALANCED,
    val directOnly: Boolean = true,
    val seatFilters: SeatFilterPrefs = SeatFilterPrefs(),
    val forceFullScan: Boolean = false,
    val debugWebViewEnabled: Boolean = false,
    val favorites: List<FavoriteRoute> = emptyList(),
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
            favorites = loadFavorites(),
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

    private fun loadFavorites(): List<FavoriteRoute> {
        val raw = prefs.getString("favorite_routes_json", null) ?: return emptyList()
        return try {
            val arr = JSONArray(raw)
            val out = ArrayList<FavoriteRoute>(arr.length())
            for (i in 0 until arr.length()) {
                val o = arr.optJSONObject(i) ?: continue
                val f = o.optString("from", "").trim()
                val t = o.optString("to", "").trim()
                if (f.isNotEmpty() && t.isNotEmpty()) {
                    out.add(FavoriteRoute(f, t))
                }
            }
            out.take(4)
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun saveFavorites(list: List<FavoriteRoute>) {
        val arr = JSONArray()
        for (fav in list.take(4)) {
            arr.put(
                JSONObject().apply {
                    put("from", fav.fromStation)
                    put("to", fav.toStation)
                }
            )
        }
        prefs.edit().putString("favorite_routes_json", arr.toString()).apply()
    }

    private fun validateFavoriteRoute(
        from: String,
        to: String,
        excludeIndex: Int? = null
    ): Pair<FavoriteRoute?, String?> {
        val fromTrim = from.trim()
        val toTrim = to.trim()
        if (fromTrim.isEmpty() || toTrim.isEmpty()) {
            return null to "Wpisz stację początkową i docelową."
        }
        if (fromTrim.equals(toTrim, ignoreCase = true)) {
            return null to "Stacja początkowa i docelowa muszą być różne."
        }
        val duplicate = _uiState.value.favorites.withIndex().any { (idx, existing) ->
            idx != excludeIndex &&
                existing.fromStation.equals(fromTrim, ignoreCase = true) &&
                existing.toStation.equals(toTrim, ignoreCase = true)
        }
        if (duplicate) {
            return null to "To połączenie jest już na liście ulubionych."
        }
        return FavoriteRoute(fromTrim, toTrim) to null
    }

    fun addFavorite(from: String, to: String): String? {
        val st = _uiState.value
        if (st.favorites.size >= 4) {
            return "Możesz zapisać maksymalnie 4 ulubione połączenia."
        }
        val (route, err) = validateFavoriteRoute(from, to)
        if (route == null) return err
        val updated = st.favorites + route
        saveFavorites(updated)
        _uiState.update { it.copy(favorites = updated) }
        return null
    }

    fun updateFavorite(index: Int, from: String, to: String): String? {
        val st = _uiState.value
        if (index !in st.favorites.indices) {
            return "Nie znaleziono połączenia do edycji."
        }
        val (route, err) = validateFavoriteRoute(from, to, excludeIndex = index)
        if (route == null) return err
        val updated = st.favorites.toMutableList().apply { set(index, route) }
        saveFavorites(updated)
        _uiState.update { it.copy(favorites = updated) }
        return null
    }

    suspend fun searchStationsForFavorites(query: String): List<StationInfo> {
        val trimmed = query.trim()
        if (trimmed.length < 2) return emptyList()
        return withContext(Dispatchers.IO) {
            stationsRepo.searchStations(trimmed, 8)
        }
    }

    fun applyFavorite(fav: FavoriteRoute) {
        _uiState.update {
            it.copy(
                fromQuery = fav.fromStation,
                toQuery = fav.toStation,
                fromSuggestions = emptyList(),
                toSuggestions = emptyList()
            )
        }
    }

    fun removeFavorite(fav: FavoriteRoute) {
        val updated = _uiState.value.favorites.filterNot {
            it.fromStation.equals(fav.fromStation, ignoreCase = true) &&
                it.toStation.equals(fav.toStation, ignoreCase = true)
        }
        saveFavorites(updated)
        _uiState.update { it.copy(favorites = updated) }
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
        val depTime = item.availabilityMap.segments.firstOrNull()?.departureTime?.ifBlank { "12:00" } ?: "12:00"
        _uiState.update {
            it.copy(
                selectedStrategyIdx = 0,
                activeScreen = AppScreen.SCAN_RESULT,
                fromQuery = item.fromStation,
                toQuery = item.toStation,
                travelDate = item.travelDate,
                ticketClass = item.ticketClass,
                lastScannedTrain = TrainConnection(
                    trainNumber = item.trainNumber,
                    departure = depTime,
                    arrival = "",
                    duration = "",
                    changes = 0
                )
            )
        }
    }

    fun rescanHistoryItem(item: ScanHistoryItem) {
        val st = _uiState.value
        val depTime = item.availabilityMap.segments.firstOrNull()?.departureTime?.ifBlank { "12:00" } ?: "12:00"
        val conn = TrainConnection(
            trainNumber = item.trainNumber,
            departure = depTime,
            arrival = "",
            duration = "",
            changes = 0
        )
        _uiState.update {
            it.copy(
                selectedStrategyIdx = 0,
                activeScreen = AppScreen.SCAN_RESULT,
                fromQuery = item.fromStation,
                toQuery = item.toStation,
                travelDate = item.travelDate,
                ticketClass = item.ticketClass,
                lastScannedTrain = conn
            )
        }
        ScanForegroundService.startScan(
            context = getApplication<Application>().applicationContext,
            fromStation = item.fromStation,
            toStation = item.toStation,
            travelDate = item.travelDate,
            trainNumber = item.trainNumber,
            depTime = depTime,
            ticketClass = item.ticketClass,
            profile = st.profile,
            forceFull = st.forceFullScan,
            seatFilters = st.seatFilters
        )
    }

    fun refreshHistory() {
        ScanHistoryStore.refresh(getApplication<Application>().applicationContext)
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
