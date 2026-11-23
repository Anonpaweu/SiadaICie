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
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
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
from backend.app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    HealthResponse,
    LiveOptimizeRequest,
    ManualMapRequest,
    OptimizationResponse,
    SeatAvailability,
    StationSearchResult,
    StrategyResponse,
    TrainRouteResponse,
    TrainStopItem,
)
from backend.app.optimizer.solver import OptimizerConfig, SeatMeta, parse_profile, solve_top_strategies
from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
from backend.app.scraper.ic_client import GraphicReservationUnavailableError, PKPIntercityScraper
from backend.app.scraper.mock_provider import (
    scenario_classic_b_c_block,
    scenario_overlapping_windows_and_proximity,
    scenario_standing_corridor_fallback,
)
from backend.app.scraper.route_resolver import build_route_segments, filter_and_merge_stops
from backend.app.scraper.schedule_scraper import fetch_live_train_connections, resolve_real_route

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize SQLite schema and auto-seed official stations table on fresh startup."""
    init_db()
    ensure_stations_seeded()
    yield


app = FastAPI(
    title="PKP Seat Optimizer API",
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
        self.last_error: Optional[str] = None

    def reset(self):
        self.progress = 0
        self.message = "Inicjalizacja skanowania..."
        self.logs = []
        self.is_running = True
        self.last_error = None

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

    def error(self, err: str):
        self.is_running = False
        self.last_error = err
        self.logs.append(f"[BŁĄD] {err}")


global_scan_tracker = ScanStatusTracker()


def _build_optimization_response(
    avail: Dict[str, List[int]],
    meta: Dict[str, SeatMeta],
    station_names: List[str],
    segment_durations: List[int],
    profile: str = "BALANCED",
    departure_times: Optional[List[str]] = None,
    raw_availability_report: Optional[str] = None,
    segment_statuses: Optional[List[dict]] = None,
) -> OptimizationResponse:
    """Shared helper to solve optimal strategies and build the standardized API response."""
    config = OptimizerConfig(profile=parse_profile(profile))
    strategies = solve_top_strategies(avail, meta, station_names, segment_durations, config=config)

    segments_info = []
    for i in range(len(station_names) - 1):
        dep_time = departure_times[i] if (departure_times and i < len(departure_times)) else ""
        status_entry = segment_statuses[i] if (segment_statuses and i < len(segment_statuses)) else {}
        seg_status = status_entry.get("status", "OK") if isinstance(status_entry, dict) else "OK"
        if hasattr(seg_status, "value"):
            seg_status = seg_status.value
        seg_error_reason = status_entry.get("error_reason", None) if isinstance(status_entry, dict) else None
        segments_info.append(
            {
                "seg_idx": i,
                "from_station": station_names[i],
                "to_station": station_names[i + 1],
                "duration_min": segment_durations[i] if i < len(segment_durations) else 30,
                "departure_time": dep_time,
                "status": seg_status or "OK",
                "error_reason": seg_error_reason,
            }
        )

    seat_matrix = [
        SeatAvailability(
            seat_uid=seat_uid,
            carriage_no=meta[seat_uid].carriage_no,
            seat_no=meta[seat_uid].seat_no,
            availability=availability,
        )
        for seat_uid, availability in avail.items()
    ]

    strategy_responses = [
        StrategyResponse(
            rank=s.rank,
            title=s.title,
            total_score=s.total_score,
            transfers_count=s.transfers_count,
            total_standing_min=s.total_standing_min,
            carriages_used=s.carriages_used,
            steps=[step.model_dump() for step in s.steps],
            purchase_instructions=s.purchase_instructions,
            tickets_to_buy=s.tickets_to_buy,
        )
        for s in strategies
    ]
    top_purchase_instructions = strategy_responses[0].purchase_instructions if strategy_responses else []

    return OptimizationResponse(
        stations=station_names,
        segments=segments_info,
        strategies=strategy_responses,
        seat_matrix=seat_matrix,
        raw_availability_report=raw_availability_report,
        purchase_instructions=top_purchase_instructions,
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
        departure_times=departure_times,
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
        departure_times=departure_times,
        raw_availability_report=request.raw_text,
        segment_statuses=segment_statuses,
    )


@app.get("/api/v1/scan-status")
async def get_scan_status():
    """Returns current live train scan progress and log entries."""
    return {
        "is_running": global_scan_tracker.is_running,
        "progress": global_scan_tracker.progress,
        "message": global_scan_tracker.message,
        "logs": global_scan_tracker.logs[-25:],
        "error": global_scan_tracker.last_error,
    }


@app.post("/api/v1/optimize", response_model=OptimizationResponse)
async def optimize_live(request: LiveOptimizeRequest):
    """
    Live optimization mode:
    Scrapes real-time seat availability segment-by-segment using Playwright
    and calculates optimal seating and transfer strategy.
    """
    global_scan_tracker.reset()
    is_headless = (
        not request.show_browser
        if request.show_browser is not None
        else (request.headless if request.headless is not None else True)
    )

    scanner = LiveTrainScanner(
        headless=is_headless,
        show_browser=request.show_browser,
        on_progress=lambda m: global_scan_tracker.update(m),
    )
    try:
        avail_map = await scanner.scan_full_train(
            from_station=request.from_station,
            to_station=request.to_station,
            travel_date=request.date,
            train_number=request.train_number or "IC 3520",
            departure_time=request.time,
            ticket_class=request.ticket_class,
        )
    except GraphicReservationUnavailableError as e:
        err_msg = f"Brak możliwości wyboru miejsca ze schematu na trasie {request.from_station} -> {request.to_station}: {str(e)}"
        global_scan_tracker.error(err_msg)
        raise HTTPException(
            status_code=502,
            detail=f"{err_msg}. Zapisano zrzut diagnostyczny do logs/debug_snapshots/error_85_percent.png.",
        )
    except Exception as e:
        err_msg = f"Błąd połączenia z systemem rezerwacyjnym podczas skanowania odcinka: {str(e)}"
        global_scan_tracker.error(err_msg)
        raise HTTPException(
            status_code=502,
            detail=f"{err_msg}. Zapisano zrzut diagnostyczny do logs/debug_snapshots/error_85_percent.png.",
        )

    avail, meta, station_names, segment_durations = availability_map_to_matrix(avail_map)
    if not avail:
        err_msg = f"System rezerwacyjny nie udostępnił wolnych miejsc w Klasie {request.ticket_class} dla wybranego pociągu (brak miejsc ze schematu)."
        global_scan_tracker.error(err_msg)
        raise HTTPException(
            status_code=502,
            detail=f"{err_msg} Zapisano zrzut diagnostyczny do logs/debug_snapshots/error_85_percent.png.",
        )

    global_scan_tracker.complete()
    formatted_report = format_availability_map(avail_map)
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
        departure_times=departure_times,
        raw_availability_report=formatted_report,
        segment_statuses=segment_statuses,
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


@app.get("/")
async def root():
    """Serve the single-page dashboard."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "PKP Seat Optimizer API running. Visit /docs."}
