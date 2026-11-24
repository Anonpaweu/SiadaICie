package pl.iccbagno.app.ui

import android.app.Application
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
import pl.iccbagno.app.data.StationsRepository
import pl.iccbagno.app.domain.StationInfo
import pl.iccbagno.app.domain.TrainConnection
import pl.iccbagno.app.optimizer.OptimizationProfile
import pl.iccbagno.app.scraper.ScheduleClient
import pl.iccbagno.app.service.ScanForegroundService
import pl.iccbagno.app.service.ScanProgressState
import java.time.LocalDate
import java.time.format.DateTimeFormatter

data class SearchUiState(
    val fromQuery: String = "Warszawa Centralna",
    val toQuery: String = "Kraków Główny",
    val fromSuggestions: List<StationInfo> = emptyList(),
    val toSuggestions: List<StationInfo> = emptyList(),
    val travelDate: String = LocalDate.now().plusDays(1).format(DateTimeFormatter.ISO_LOCAL_DATE),
    val minDepartureTime: String = "00:00",
    val ticketClass: Int = 2,
    val profile: OptimizationProfile = OptimizationProfile.BALANCED,
    val directOnly: Boolean = true,
    val forceFullScan: Boolean = false,
    val debugWebViewEnabled: Boolean = false,
    val isLoadingTrains: Boolean = false,
    val trainsError: String? = null,
    val trains: List<TrainConnection> = emptyList(),
    val selectedStrategyIdx: Int = 0
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val stationsRepo = StationsRepository(application.applicationContext)
    private val scheduleClient = ScheduleClient(stationsRepo)

    private val _uiState = MutableStateFlow(SearchUiState())
    val uiState: StateFlow<SearchUiState> = _uiState.asStateFlow()

    val scanState: StateFlow<ScanProgressState> = ScanForegroundService.state

    private var fromSearchJob: Job? = null
    private var toSearchJob: Job? = null

    fun onFromQueryChange(q: String) {
        _uiState.update { it.copy(fromQuery = q) }
        fromSearchJob?.cancel()
        if (q.trim().length < 2) {
            _uiState.update { it.copy(fromSuggestions = emptyList()) }
            return
        }
        fromSearchJob = viewModelScope.launch(Dispatchers.IO) {
            delay(150)
            val list = stationsRepo.searchStations(q, 12)
            _uiState.update { it.copy(fromSuggestions = list) }
        }
    }

    fun onToQueryChange(q: String) {
        _uiState.update { it.copy(toQuery = q) }
        toSearchJob?.cancel()
        if (q.trim().length < 2) {
            _uiState.update { it.copy(toSuggestions = emptyList()) }
            return
        }
        toSearchJob = viewModelScope.launch(Dispatchers.IO) {
            delay(150)
            val list = stationsRepo.searchStations(q, 12)
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

    fun setProfile(profile: OptimizationProfile) {
        _uiState.update { it.copy(profile = profile, selectedStrategyIdx = 0) }
        viewModelScope.launch(Dispatchers.Default) {
            ScanForegroundService.recomputeProfile(profile)
        }
    }

    fun setDirectOnly(directOnly: Boolean) {
        _uiState.update { it.copy(directOnly = directOnly) }
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
        _uiState.update { it.copy(selectedStrategyIdx = 0) }
        ScanForegroundService.startScan(
            context = getApplication<Application>().applicationContext,
            fromStation = st.fromQuery.trim(),
            toStation = st.toQuery.trim(),
            travelDate = st.travelDate,
            trainNumber = train.trainNumber,
            depTime = train.departure,
            ticketClass = st.ticketClass,
            profile = st.profile,
            forceFull = st.forceFullScan
        )
    }

    fun cancelScan() {
        ScanForegroundService.cancelScan(getApplication<Application>().applicationContext)
    }
}
