"""Pydantic schemas for API request/response models."""
from typing import List, Optional
from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    from_station: str
    to_station: str
    date: str = "2026-11-15"
    train_no: str = "IC 3520"
    dep_time: Optional[str] = None
    mode: str = "mock_classic"  # "mock_classic", "mock_overlap", "mock_standing", "live"
    profile: str = "BALANCED"  # "BALANCED", "MINIMAL_WALKING", "MINIMAL_TRANSFERS"
    ticket_class: int = Field(default=2, ge=1, le=2)
    headless: Optional[bool] = None
    show_browser: Optional[bool] = None


class StepResponse(BaseModel):
    seat_uid: str
    carriage_no: int
    seat_no: str
    from_station_idx: int
    to_station_idx: int
    from_station_name: str
    to_station_name: str
    duration_min: int
    is_standing: bool = False
    transfer_instruction: Optional[str] = None
    compartment: Optional[int] = None
    alternative_seats_in_same_carriage: List[str] = []
    booking_url: Optional[str] = None


SeatBlockSchema = StepResponse


class StrategyResponse(BaseModel):
    rank: int
    title: str
    total_score: float
    transfers_count: int
    total_standing_min: int
    carriages_used: List[int]
    steps: List[StepResponse]
    purchase_instructions: List[str] = []
    tickets_to_buy: List[str] = []


RouteRecommendationSchema = StrategyResponse


class SeatAvailability(BaseModel):
    seat_uid: str
    carriage_no: int
    seat_no: str
    availability: List[int]


class AnalyzeResponse(BaseModel):
    stations: List[str]
    segments: List[dict]
    strategies: List[StrategyResponse]
    seat_matrix: List[SeatAvailability]
    raw_availability_report: Optional[str] = None
    purchase_instructions: List[str] = []
    fast_path_used: bool = False
    total_route_segments: int = 0


OptimizationResponse = AnalyzeResponse


class ManualMapRequest(BaseModel):
    raw_text: str
    profile: str = "BALANCED"


class LiveOptimizeRequest(BaseModel):
    from_station: str
    to_station: str
    date: str
    time: Optional[str] = "12:00"
    train_number: Optional[str] = "IC 3520"
    ticket_class: int = Field(default=2, ge=1, le=2)
    profile: str = "BALANCED"
    headless: bool = True
    show_browser: Optional[bool] = None
    force_full_scan: bool = False
    scan_id: Optional[str] = None


class CancelScanRequest(BaseModel):
    scan_id: Optional[str] = None


class TrainStopItem(BaseModel):
    seq: int
    name: str
    arrival_time: str = ""
    departure_time: str = ""
    eva_code: str = ""


class TrainRouteResponse(BaseModel):
    train_number: str
    travel_date: str
    stops: List[TrainStopItem]
    formatted_route: str


class HealthResponse(BaseModel):
    status: str = "ok"


class StationSearchResult(BaseModel):
    name: str
    eva_code: str
