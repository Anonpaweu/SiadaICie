"""
FastAPI application entrypoint for PKP Seat Optimizer.

Endpoints:
- GET  /api/health — health check
- GET  /api/stations/search?q=... — station autocomplete
- POST /api/analyze — run seat optimization (mock / cached live)
- POST /api/v1/optimize — run live Playwright seat scanner and optimizer
- POST /api/v1/optimize-from-text — parse manual text availability report
- GET  /api/v1/scan-status — live scan progress and console logs
- GET  /api/v1/train-route — intermediate stops for a train
- GET/POST /api/v1/trains — live train connections for a route and date
"""
import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.db.database import ensure_stations_seeded, get_connection, init_db
from backend.app.db.seed_stations import normalize_polish_name
from backend.app.engine.availability_parser import (
    availability_map_to_matrix,
    format_availability_map,
    parse_availability_text,
)
from backend.app.models.domain import SeatFilterPrefs
from backend.app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    CancelScanRequest,
    HealthResponse,
    LiveOptimizeRequest,
    ManualMapRequest,
    OptimizationResponse,
    RecomputeRequest,
    SeatAvailability,
    StationSearchResult,
    StrategyResponse,
    TrainRouteResponse,
    TrainStopItem,
)
from backend.app.optimizer.solver import OptimizerConfig, SeatMeta, format_transfers_pl, parse_profile, solve_top_strategies
from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
from backend.app.scraper.grm_parser import TAG_BIKE, build_eic_search_url, is_seat_allowed
from backend.app.scraper.ic_client import GraphicReservationUnavailableError, PKPIntercityScraper
from backend.app.scraper.non_grm_scanner import check_grm_available, scan_non_grm_train
from backend.app.scraper.mock_provider import (
    scenario_classic_b_c_block,
    scenario_overlapping_windows_and_proximity,
    scenario_standing_corridor_fallback,
)
from backend.app.scraper.route_resolver import build_route_segments, filter_and_merge_stops
from backend.app.scraper.schedule_scraper import (
    fetch_live_train_connections,
    get_station_info,
    resolve_real_route,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize SQLite schema and auto-seed official stations table on fresh startup."""
    init_db()
    ensure_stations_seeded()
    yield


app = FastAPI(
    title="SiadaICie API",
    version="0.2.0",
    description="System optymalizacji miejsc siedzących i przesiadek w pociągach dalekobieżnych.",
    lifespan=lifespan,
)

# Restrict CORS to local development origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Station fallback dictionary (verified official EVA codes from pkp_optimizer.db / station.js)
KNOWN_STATIONS = [
    {"name": "Warszawa Centralna", "eva_code": "5100065"},
    {"name": "Warszawa Zachodnia", "eva_code": "5100067"},
    {"name": "Warszawa Wschodnia", "eva_code": "5100066"},
    {"name": "Kraków Główny", "eva_code": "5100028"},
    {"name": "Gdańsk Główny", "eva_code": "5100009"},
    {"name": "Gdynia Główna", "eva_code": "5100010"},
    {"name": "Poznań Główny", "eva_code": "5100081"},
    {"name": "Wrocław Główny", "eva_code": "5100069"},
    {"name": "Katowice", "eva_code": "5100020"},
    {"name": "Łódź Fabryczna", "eva_code": "5100317"},
    {"name": "Sopot", "eva_code": "5100056"},
    {"name": "Gdańsk Wrzeszcz", "eva_code": "5101307"},
    {"name": "Kutno", "eva_code": "5100032"},
    {"name": "Bydgoszcz Główna", "eva_code": "5100005"},
]

SCENARIO_LOADERS = {
    "mock_classic": scenario_classic_b_c_block,
    "mock_overlap": scenario_overlapping_windows_and_proximity,
    "mock_standing": scenario_standing_corridor_fallback,
}


class ScanStatusTracker:
    def __init__(self):
        self.progress: int = 0
        self.message: str = ""
        self.logs: List[str] = []
        self.is_running: bool = False
        self.cancelled: bool = False
        self.last_error: Optional[str] = None
        self.cancel_event: asyncio.Event = asyncio.Event()
        self.task: Optional[asyncio.Task] = None

    def reset(self):
        self.progress = 0
        self.message = "Inicjalizacja skanowania..."
        self.logs = []
        self.is_running = True
        self.cancelled = False
        self.last_error = None
        self.cancel_event = asyncio.Event()
        self.task = None

    def update(self, msg: str):
        self.logs.append(msg)
        match = re.search(r"\[(\d+)%\]", msg)
        if match:
            pct = min(100, max(0, int(match.group(1))))
            # Monotonic progress so concurrent workers never move the progress bar backwards
            self.progress = max(self.progress, pct)
        self.message = msg

    def complete(self):
        self.progress = 100
        self.message = "Zakończono pomyślnie!"
        self.is_running = False

    def cancel(self):
        self.cancelled = True
        self.is_running = False
        self.message = "Skanowanie anulowane przez użytkownika."
        self.logs.append("[ANULOWANO] Przerwano skanowanie na żądanie użytkownika.")
        self.cancel_event.set()
        if self.task and not self.task.done():
            self.task.cancel()

    def error(self, err: str):
        self.is_running = False
        self.last_error = err
        self.logs.append(f"[BŁĄD] {err}")


class ScanJobManager:
    """Manages per-scan trackers keyed by scan_id while retaining backwards-compatible latest lookup."""

    def __init__(self, max_jobs: int = 50):
        self.jobs: Dict[str, ScanStatusTracker] = {}
        self.latest_scan_id: Optional[str] = None
        self.max_jobs = max_jobs

    def create_job(self, scan_id: str) -> ScanStatusTracker:
        tracker = ScanStatusTracker()
        tracker.reset()
        self.jobs[scan_id] = tracker
        self.latest_scan_id = scan_id
        while len(self.jobs) > self.max_jobs:
            oldest = next(iter(self.jobs))
            if oldest != self.latest_scan_id:
                self.jobs.pop(oldest, None)
            else:
                break
        return tracker

    def get_job(self, scan_id: Optional[str] = None):
        if scan_id:
            tracker = self.jobs.get(scan_id)
            return (scan_id, tracker) if tracker is not None else None
        if self.latest_scan_id and self.latest_scan_id in self.jobs:
            return self.latest_scan_id, self.jobs[self.latest_scan_id]
        return None

    def cancel_job(self, scan_id: Optional[str] = None) -> Optional[str]:
        found = self.get_job(scan_id)
        if found is None:
            return None
        target_id, tracker = found
        tracker.cancel()
        return target_id


global_scan_tracker = ScanStatusTracker()
scan_manager = ScanJobManager()
_last_cached_analysis: Optional[dict] = None


def _aggregate_standing_status(statuses: List[Optional[str]]) -> Optional[str]:
    if "OVERBOOKED" in statuses:
        return "OVERBOOKED"
    if "SPECIAL_ONLY" in statuses:
        return "SPECIAL_ONLY"
    if "AVAILABLE" in statuses:
        return "AVAILABLE"
    return None


def _compute_bike_suggestion(
    avail: Dict[str, List[int]],
    meta: Dict[str, SeatMeta],
    station_names: List[str],
    segment_durations: List[int],
    profile: str,
    active_filters: SeatFilterPrefs,
    current_strategies: list,
) -> Optional[str]:
    """
    Suggests enabling `allow_bike` (strictly for BIKE seats only, never other special categories)
    when `allow_bike` is False and enabling it strictly reduces standing time or transfers.
    """
    if getattr(active_filters, "allow_bike", True) or not avail:
        return None
    if not any(TAG_BIKE in getattr(m, "seat_tags", []) for m in meta.values()):
        return None

    bike_filters = SeatFilterPrefs(
        allow_bike=True,
        allow_pregnant=bool(getattr(active_filters, "allow_pregnant", False)),
        allow_child=bool(getattr(active_filters, "allow_child", False)),
        allow_elderly=bool(getattr(active_filters, "allow_elderly", False)),
        allow_prm_assist=bool(getattr(active_filters, "allow_prm_assist", False)),
        allow_wheelchair=bool(getattr(active_filters, "allow_wheelchair", False)),
    )
    bike_cfg = OptimizerConfig(profile=parse_profile(profile), seat_filters=bike_filters)
    bike_strategies = solve_top_strategies(
        avail, meta, station_names, segment_durations, config=bike_cfg, top_k=1
    )
    if not bike_strategies:
        return None

    best_bike = bike_strategies[0]
    best_cur = current_strategies[0] if current_strategies else None
    is_better = (
        best_cur is None
        or best_bike.total_standing_min < best_cur.total_standing_min
        or (
            best_bike.total_standing_min == best_cur.total_standing_min
            and best_bike.transfers_count < best_cur.transfers_count
        )
    )
    if not is_better:
        return None

    if best_bike.transfers_count == 0 and best_bike.total_standing_min == 0:
        return "Z miejscem rowerowym pojedziesz 100% trasy na siedząco bez przesiadek."
    if best_cur is not None and best_bike.total_standing_min < best_cur.total_standing_min:
        return (
            f"Z miejscem rowerowym skrócisz stanie z {best_cur.total_standing_min} min "
            f"do {best_bike.total_standing_min} min ({format_transfers_pl(best_bike.transfers_count)})."
        )
    return (
        f"Z miejscem rowerowym masz wygodniejszy wariant: "
        f"{format_transfers_pl(best_bike.transfers_count)}, stanie {best_bike.total_standing_min} min."
    )


def _build_optimization_response(
    avail: Dict[str, List[int]],
    meta: Dict[str, SeatMeta],
    station_names: List[str],
    segment_durations: List[int],
    profile: str = "BALANCED",
    seat_filters: Optional[SeatFilterPrefs] = None,
    departure_times: Optional[List[str]] = None,
    raw_availability_report: Optional[str] = None,
    segment_statuses: Optional[List[dict]] = None,
    travel_date: Optional[str] = None,
    station_evas: Optional[Dict[str, str]] = None,
    segment_dates: Optional[List[str]] = None,
    fast_path_used: bool = False,
    total_route_segments: int = 0,
    full_route_standing_status: Optional[str] = None,
) -> OptimizationResponse:
    """Shared helper to solve optimal strategies and build the standardized API response."""
    global _last_cached_analysis
    active_filters = seat_filters or SeatFilterPrefs()
    _last_cached_analysis = {
        "avail": avail,
        "meta": meta,
        "station_names": station_names,
        "segment_durations": segment_durations,
        "departure_times": departure_times,
        "raw_availability_report": raw_availability_report,
        "segment_statuses": segment_statuses,
        "travel_date": travel_date,
        "station_evas": station_evas,
        "segment_dates": segment_dates,
        "fast_path_used": fast_path_used,
        "total_route_segments": total_route_segments,
        "full_route_standing_status": full_route_standing_status,
    }
    config = OptimizerConfig(profile=parse_profile(profile), seat_filters=active_filters)
    strategies = solve_top_strategies(avail, meta, station_names, segment_durations, config=config)

    segments_info = []
    for i in range(len(station_names) - 1):
        dep_time = departure_times[i] if (departure_times and i < len(departure_times)) else ""
        status_entry = segment_statuses[i] if (segment_statuses and i < len(segment_statuses)) else {}
        seg_status = status_entry.get("status", "OK") if isinstance(status_entry, dict) else "OK"
        if hasattr(seg_status, "value"):
            seg_status = seg_status.value
        seg_error_reason = status_entry.get("error_reason", None) if isinstance(status_entry, dict) else None
        seg_standing_status = status_entry.get("standing_status", None) if isinstance(status_entry, dict) else None
        segments_info.append(
            {
                "seg_idx": i,
                "from_station": station_names[i],
                "to_station": station_names[i + 1],
                "duration_min": segment_durations[i] if i < len(segment_durations) else 30,
                "departure_time": dep_time,
                "status": seg_status or "OK",
                "error_reason": seg_error_reason,
                "standing_status": seg_standing_status,
            }
        )

    seat_matrix = [
        SeatAvailability(
            seat_uid=seat_uid,
            carriage_no=meta[seat_uid].carriage_no,
            seat_no=meta[seat_uid].seat_no,
            seat_tags=list(meta[seat_uid].seat_tags),
            availability=availability,
        )
        for seat_uid, availability in avail.items()
        if seat_uid in meta and is_seat_allowed(meta[seat_uid].seat_tags, active_filters)
    ]

    def _resolve_eva(name: str) -> str:
        eva = (station_evas or {}).get(name) or ""
        if not eva and name:
            _, eva = get_station_info(name)
        return eva

    strategy_responses = []
    for s in strategies:
        steps_payload = []
        for step in s.steps:
            step_dict = step.model_dump()
            if step.is_standing:
                seg_statuses_for_step = [
                    segments_info[i].get("standing_status")
                    for i in range(max(0, step.from_station_idx), min(len(segments_info), step.to_station_idx))
                ]
                agg_standing = _aggregate_standing_status(seg_statuses_for_step)
                if agg_standing is None and avail:
                    start_i = max(0, step.from_station_idx)
                    end_i = min(len(segments_info), step.to_station_idx)
                    if start_i < end_i and all(
                        any(
                            k < len(vec)
                            and vec[k] == 1
                            and uid in meta
                            and not is_seat_allowed(meta[uid].seat_tags, active_filters)
                            for uid, vec in avail.items()
                        )
                        for k in range(start_i, end_i)
                    ):
                        agg_standing = "SPECIAL_ONLY"
                step_dict["standing_status"] = agg_standing
            if travel_date:
                from_eva = _resolve_eva(step.from_station_name)
                to_eva = _resolve_eva(step.to_station_name)
                if from_eva and to_eva:
                    dep_time = (
                        departure_times[step.from_station_idx]
                        if (
                            departure_times
                            and 0 <= step.from_station_idx < len(departure_times)
                            and departure_times[step.from_station_idx]
                        )
                        else "08:00"
                    )
                    step_date = (
                        segment_dates[step.from_station_idx]
                        if (
                            segment_dates
                            and 0 <= step.from_station_idx < len(segment_dates)
                            and segment_dates[step.from_station_idx]
                        )
                        else travel_date
                    )
                    step_dict["booking_url"] = build_eic_search_url(
                        from_eva=from_eva,
                        to_eva=to_eva,
                        date_str=step_date,
                        departure_time=dep_time,
                        direct_only=True,
                    )
            steps_payload.append(step_dict)

        strategy_responses.append(
            StrategyResponse(
                rank=s.rank,
                title=s.title,
                total_score=s.total_score,
                transfers_count=s.transfers_count,
                total_standing_min=s.total_standing_min,
                carriages_used=s.carriages_used,
                steps=steps_payload,
                purchase_instructions=s.purchase_instructions,
                tickets_to_buy=s.tickets_to_buy,
            )
        )
    top_purchase_instructions = strategy_responses[0].purchase_instructions if strategy_responses else []

    full_route_booking_url: Optional[str] = None
    if travel_date and len(station_names) >= 2:
        first_eva = _resolve_eva(station_names[0])
        last_eva = _resolve_eva(station_names[-1])
        if first_eva and last_eva:
            first_dep = departure_times[0] if (departure_times and departure_times[0]) else "08:00"
            first_date = segment_dates[0] if (segment_dates and segment_dates[0]) else travel_date
            full_route_booking_url = build_eic_search_url(
                from_eva=first_eva,
                to_eva=last_eva,
                date_str=first_date,
                departure_time=first_dep,
                direct_only=True,
            )

    bike_suggestion = _compute_bike_suggestion(
        avail=avail,
        meta=meta,
        station_names=station_names,
        segment_durations=segment_durations,
        profile=profile,
        active_filters=active_filters,
        current_strategies=strategies,
    )

    return OptimizationResponse(
        stations=station_names,
        segments=segments_info,
        strategies=strategy_responses,
        seat_matrix=seat_matrix,
        raw_availability_report=raw_availability_report,
        purchase_instructions=top_purchase_instructions,
        fast_path_used=fast_path_used,
        total_route_segments=total_route_segments or len(segments_info),
        full_route_booking_url=full_route_booking_url,
        full_route_standing_status=full_route_standing_status,
        bike_suggestion=bike_suggestion,
    )


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="ok")


@app.get("/api/stations/search", response_model=List[StationSearchResult])
def search_stations(q: str = Query(..., min_length=1)):
    """Search for stations matching the query string in the SQLite stations database."""
    init_db()
    norm_q = normalize_polish_name(q)
    if not norm_q:
        return []
    prefix_pattern = f"{norm_q}%"
    infix_pattern = f"%{norm_q}%"
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT name, eva_code
            FROM stations
            WHERE normalized_name = ? OR normalized_name LIKE ? OR normalized_name LIKE ?
            ORDER BY
                CASE
                    WHEN normalized_name = ? THEN 0
                    WHEN normalized_name LIKE ? THEN 1
                    ELSE 2
                END ASC,
                is_main_hub DESC,
                length(name) ASC,
                name ASC
            LIMIT 20
            """,
            (norm_q, prefix_pattern, infix_pattern, norm_q, prefix_pattern),
        )
        rows = cur.fetchall()
        if rows:
            return [StationSearchResult(name=row["name"], eva_code=row["eva_code"]) for row in rows]
    finally:
        conn.close()

    results = [
        StationSearchResult(name=s["name"], eva_code=s["eva_code"])
        for s in KNOWN_STATIONS
        if norm_q in normalize_polish_name(s["name"])
    ]
    return results[:15]


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """Run seat optimization for mock scenarios or cached live mode."""
    departure_times: List[str] = []
    station_evas: Optional[Dict[str, str]] = None
    travel_date: Optional[str] = None
    if request.mode == "live":
        is_headless = (
            not request.show_browser
            if request.show_browser is not None
            else (request.headless if request.headless is not None else True)
        )
        scraper = PKPIntercityScraper(headless=is_headless)
        try:
            stops = await scraper.fetch_train_stops(
                request.from_station,
                request.to_station,
                request.date,
                request.train_no,
                dep_time=request.dep_time,
            )
            station_names = [s.name for s in stops]
            station_evas = {s.name: s.eva_code for s in stops if getattr(s, "eva_code", "")}
            travel_date = request.date
            segments = build_route_segments(stops)
            segment_durations = [seg.duration_min for seg in segments]
            departure_times = [seg.departure_time for seg in segments]
            avail, meta = await scraper.generate_live_seat_matrix(request.train_no, request.date, stops)
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Błąd analizy trasy: {str(e)}",
            )
    else:
        loader = SCENARIO_LOADERS.get(request.mode) or scenario_classic_b_c_block
        avail, meta, station_names, segment_durations = loader()

    return _build_optimization_response(
        avail=avail,
        meta=meta,
        station_names=station_names,
        segment_durations=segment_durations,
        profile=request.profile,
        seat_filters=request.seat_filters,
        departure_times=departure_times,
        travel_date=travel_date,
        station_evas=station_evas,
    )


@app.post("/api/v1/optimize-from-text", response_model=OptimizationResponse)
def optimize_from_text(request: ManualMapRequest):
    """
    Parses plain text report of seats (format: 'Piotrków - Radomsko 15:12\\nW5 m82\\n...')
    and calculates optimal seating and transfer strategy.
    """
    if not request.raw_text or not request.raw_text.strip():
        raise HTTPException(
            status_code=400,
            detail="Pole raw_text nie może być puste. Wklej rozpiskę wolnych miejsc.",
        )

    try:
        avail_map = parse_availability_text(request.raw_text)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Błąd parsowania tekstu dostępności: {str(e)}",
        )

    if not avail_map.segments:
        raise HTTPException(
            status_code=400,
            detail="Nie znaleziono poprawnych odcinków w podanym tekście. Wzorzec nagłówka: 'Stacja A - Stacja B 14:45'.",
        )

    avail, meta, station_names, segment_durations = availability_map_to_matrix(avail_map)
    if not avail:
        raise HTTPException(
            status_code=400,
            detail="Nie znaleziono żadnych wolnych miejsc w podanej rozpisce.",
        )

    departure_times = [seg.departure_time for seg in avail_map.segments]
    segment_statuses = [
        {
            "status": getattr(seg, "status", "OK") or "OK",
            "error_reason": getattr(seg, "error_reason", None),
        }
        for seg in avail_map.segments
    ]

    return _build_optimization_response(
        avail=avail,
        meta=meta,
        station_names=station_names,
        segment_durations=segment_durations,
        profile=request.profile,
        seat_filters=request.seat_filters,
        departure_times=departure_times,
        raw_availability_report=request.raw_text,
        segment_statuses=segment_statuses,
    )


@app.post("/api/v1/recompute", response_model=OptimizationResponse)
def recompute_cached_analysis(request: RecomputeRequest):
    """Recomputes optimal strategies from the last scanned/analyzed matrix without re-scraping."""
    if not _last_cached_analysis:
        raise HTTPException(
            status_code=404,
            detail="Brak zapisanej analizy do przeliczenia.",
        )
    return _build_optimization_response(
        **_last_cached_analysis,
        profile=request.profile,
        seat_filters=request.seat_filters,
    )


@app.get("/api/v1/scan-status")
async def get_scan_status(scan_id: Optional[str] = Query(None)):
    """Returns current live train scan progress and log entries."""
    found = scan_manager.get_job(scan_id)
    if found is not None:
        resolved_id, tracker = found
        return {
            "scan_id": resolved_id,
            "is_running": tracker.is_running,
            "cancelled": tracker.cancelled,
            "progress": tracker.progress,
            "message": tracker.message,
            "logs": tracker.logs[-25:],
            "error": tracker.last_error,
        }
    return {
        "scan_id": None,
        "is_running": global_scan_tracker.is_running,
        "cancelled": global_scan_tracker.cancelled,
        "progress": global_scan_tracker.progress,
        "message": global_scan_tracker.message,
        "logs": global_scan_tracker.logs[-25:],
        "error": global_scan_tracker.last_error,
    }


@app.post("/api/v1/scan-cancel")
async def cancel_scan(
    request: Optional[CancelScanRequest] = Body(default=None),
    scan_id: Optional[str] = Query(None),
):
    """Cancels a running scan by scan_id (or the latest active scan if omitted)."""
    target_id = (request.scan_id if request else None) or scan_id
    cancelled_id = scan_manager.cancel_job(target_id)
    if cancelled_id is None:
        if global_scan_tracker.is_running:
            global_scan_tracker.cancel()
            return {"status": "cancelled", "scan_id": None}
        return {"status": "not_found", "scan_id": target_id}
    if target_id is None or cancelled_id == scan_manager.latest_scan_id:
        global_scan_tracker.cancel()
    return {"status": "cancelled", "scan_id": cancelled_id}


@app.post("/api/v1/optimize", response_model=OptimizationResponse)
async def optimize_live(request: LiveOptimizeRequest):
    """
    Live optimization mode:
    Scrapes real-time seat availability (Fast-Path A->B with automatic fallback to all segments)
    and calculates optimal seating and transfer strategy.
    """
    scan_id = (request.scan_id or "").strip() or uuid.uuid4().hex[:12]
    tracker = scan_manager.create_job(scan_id)
    global_scan_tracker.reset()

    is_headless = (
        not request.show_browser
        if request.show_browser is not None
        else (request.headless if request.headless is not None else True)
    )

    def _on_progress(msg: str) -> None:
        tracker.update(msg)
        global_scan_tracker.update(msg)

    train_num_val = request.train_number or "IC 3520"
    if not await asyncio.to_thread(check_grm_available, train_num_val, request.date):
        _on_progress("[5%] Wykryto pociąg bez graficznego schematu miejsc — uruchamiam tryb odcinkowy...")
        try:
            non_grm_resp = await scan_non_grm_train(
                from_station=request.from_station,
                to_station=request.to_station,
                travel_date=request.date,
                train_number=train_num_val,
                dep_time=request.time,
                ticket_class=request.ticket_class,
                on_progress=_on_progress,
            )
            tracker.complete()
            global_scan_tracker.complete()
            return non_grm_resp
        except asyncio.CancelledError:
            tracker.cancel()
            global_scan_tracker.cancel()
            raise HTTPException(
                status_code=499,
                detail="Skanowanie zostało anulowane przez użytkownika.",
            )
        except Exception as e:
            err_msg = f"Błąd podczas analizy pociągu bez schematu miejsc: {str(e)}"
            tracker.error(err_msg)
            global_scan_tracker.error(err_msg)
            raise HTTPException(status_code=502, detail=err_msg)

    scanner = LiveTrainScanner(
        headless=is_headless,
        show_browser=request.show_browser,
        on_progress=_on_progress,
    )

    scan_coro = scanner.scan_full_train(
        from_station=request.from_station,
        to_station=request.to_station,
        travel_date=request.date,
        train_number=request.train_number or "IC 3520",
        departure_time=request.time,
        ticket_class=request.ticket_class,
        fast_path=not request.force_full_scan,
        cancel_event=tracker.cancel_event,
        seat_filters=request.seat_filters,
    )
    scan_task = asyncio.create_task(scan_coro)
    tracker.task = scan_task
    global_scan_tracker.task = scan_task

    try:
        avail_map = await scan_task
    except asyncio.CancelledError:
        tracker.cancel()
        global_scan_tracker.cancel()
        raise HTTPException(
            status_code=499,
            detail="Skanowanie zostało anulowane przez użytkownika.",
        )
    except GraphicReservationUnavailableError as e:
        err_msg = f"Brak możliwości wyboru miejsca ze schematu na trasie {request.from_station} -> {request.to_station}: {str(e)}"
        tracker.error(err_msg)
        global_scan_tracker.error(err_msg)
        raise HTTPException(
            status_code=502,
            detail=f"{err_msg}. Zapisano zrzut diagnostyczny do logs/debug_snapshots/error_85_percent.png.",
        )
    except Exception as e:
        err_msg = f"Błąd połączenia z systemem rezerwacyjnym podczas skanowania odcinka: {str(e)}"
        tracker.error(err_msg)
        global_scan_tracker.error(err_msg)
        raise HTTPException(
            status_code=502,
            detail=f"{err_msg}. Zapisano zrzut diagnostyczny do logs/debug_snapshots/error_85_percent.png.",
        )

    if tracker.cancel_event.is_set() or tracker.cancelled:
        raise HTTPException(
            status_code=499,
            detail="Skanowanie zostało anulowane przez użytkownika.",
        )

    avail, meta, station_names, segment_durations = availability_map_to_matrix(avail_map)
    has_sold_out_data = any(getattr(seg, "status", "") == "SOLD_OUT" for seg in avail_map.segments)
    has_standing_info = bool(getattr(avail_map, "full_route_standing_status", None))
    if not avail and not has_sold_out_data and not has_standing_info:
        err_msg = f"System rezerwacyjny nie udostępnił wolnych miejsc w Klasie {request.ticket_class} dla wybranego pociągu (brak miejsc ze schematu)."
        tracker.error(err_msg)
        global_scan_tracker.error(err_msg)
        raise HTTPException(
            status_code=502,
            detail=f"{err_msg} Zapisano zrzut diagnostyczny do logs/debug_snapshots/error_85_percent.png.",
        )

    tracker.complete()
    global_scan_tracker.complete()
    formatted_report = format_availability_map(avail_map)
    departure_times = [seg.departure_time for seg in avail_map.segments]
    segment_statuses = [
        {
            "status": getattr(seg, "status", "OK") or "OK",
            "error_reason": getattr(seg, "error_reason", None),
            "standing_status": getattr(seg, "standing_status", None),
        }
        for seg in avail_map.segments
    ]
    station_evas: Dict[str, str] = {}
    segment_dates: List[str] = []
    for seg in avail_map.segments:
        f_eva = (getattr(seg, "from_eva", "") or "").strip()
        t_eva = (getattr(seg, "to_eva", "") or "").strip()
        if f_eva:
            station_evas[seg.from_station] = f_eva
        if t_eva:
            station_evas[seg.to_station] = t_eva
        segment_dates.append((getattr(seg, "segment_date", "") or "").strip() or request.date)

    return _build_optimization_response(
        avail=avail,
        meta=meta,
        station_names=station_names,
        segment_durations=segment_durations,
        profile=request.profile,
        seat_filters=request.seat_filters,
        departure_times=departure_times,
        raw_availability_report=formatted_report,
        segment_statuses=segment_statuses,
        travel_date=request.date,
        station_evas=station_evas,
        segment_dates=segment_dates,
        fast_path_used=bool(getattr(avail_map, "fast_path_used", False)),
        total_route_segments=int(getattr(avail_map, "total_route_segments", 0) or len(avail_map.segments)),
        full_route_standing_status=getattr(avail_map, "full_route_standing_status", None),
    )


@app.get("/api/v1/train-route", response_model=TrainRouteResponse)
def get_train_route(
    from_station: str = Query(...),
    to_station: str = Query(...),
    date: str = Query(...),
    train_number: str = Query("IC 3520"),
    dep_time: Optional[str] = Query(None),
):
    """
    Returns the real sequence of intermediate stations and arrival/departure times
    for the selected train journey (runs in threadpool to avoid blocking event loop).
    """
    try:
        stops = resolve_real_route(
            from_station=from_station,
            to_station=to_station,
            travel_date=date,
            train_number=train_number,
            dep_time=dep_time,
        )
        stops = filter_and_merge_stops(stops, merge_short_segments=False)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Nie udało się pobrać trasy dla pociągu {train_number}: {str(e)}",
        )

    route_desc = " -> ".join(
        [
            f"{idx + 1}. {s.name} ({s.departure_time or s.arrival_time or '??:??'})"
            for idx, s in enumerate(stops)
        ]
    )

    stops_items = [
        TrainStopItem(
            seq=s.seq,
            name=s.name,
            arrival_time=s.arrival_time or "",
            departure_time=s.departure_time or "",
            eva_code=s.eva_code,
        )
        for s in stops
    ]

    return TrainRouteResponse(
        train_number=train_number,
        travel_date=date,
        stops=stops_items,
        formatted_route=route_desc,
    )


@app.api_route("/api/v1/trains", methods=["GET", "POST"])
def list_trains(
    from_station: str = Query(...),
    to_station: str = Query(...),
    date: str = Query(...),
    time: str = Query("00:00"),
    direct_only: bool = Query(False),
    full_day: bool = Query(True),
):
    """Return available real trains scraped from timetable for the selected route and date."""
    try:
        real_trains = fetch_live_train_connections(
            from_station=from_station,
            to_station=to_station,
            travel_date=date,
            travel_time=time,
            direct_only=direct_only,
            full_day=full_day,
        )
        return real_trains or []
    except Exception as e:
        logger.warning(f"Failed to fetch train connections for {from_station} -> {to_station}: {e}")
        return []


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the SiadaICie logo icon without text as browser tab favicon."""
    logo_file = STATIC_DIR / "brand-logo.png"
    if logo_file.exists():
        return FileResponse(str(logo_file), media_type="image/png")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/")
async def root():
    """Serve the single-page dashboard."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "SiadaICie API running. Visit /docs."}
