"""
Domain models for PKP Intercity Seat Optimizer ("Siedzisz.pl").

Represents stations, segments, seats, carriages, train topology, and journey legs.
"""
from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class PositionType(str, Enum):
    WINDOW = "WINDOW"
    MIDDLE = "MIDDLE"
    AISLE = "AISLE"
    TABLE = "TABLE"


class SpecialZone(str, Enum):
    STANDARD = "STANDARD"
    SILENCE_ZONE = "SILENCE_ZONE"
    BIKE = "BIKE"
    DISABLED = "DISABLED"
    FAMILY = "FAMILY"


class Station(BaseModel):
    index: int
    eva_code: str
    name: str
    dep_time: Optional[str] = None
    arr_time: Optional[str] = None
    dwell_minutes: int = 0
    metropolitan_group: Optional[str] = None
    is_main_hub: bool = False


class Segment(BaseModel):
    index: int
    from_station: Station
    to_station: Station
    duration_minutes: int
    dwell_time_minutes: int = 0


class Seat(BaseModel):
    id: str                                  # np. "W10_M12"
    carriage_number: int                     # np. 10
    seat_number: str                         # np. "12"
    compartment_id: Optional[int] = None     # Numer przedziału / rzędu
    seat_class: int = 2                      # 1 lub 2
    position_type: PositionType = PositionType.MIDDLE
    special_zone: SpecialZone = SpecialZone.STANDARD
    is_standing_virtual: bool = False


class Carriage(BaseModel):
    carriage_number: int
    sequence_index: int                      # Rzeczywista kolejność w składzie (0, 1, 2...)
    unit_group_id: int = 1                   # Jednostka EZT (1 lub 2 w podwójnej trakcji)
    is_restaurant: bool = False              # Czy to wagon WARS
    car_class: int = 2
    seats: List[Seat] = Field(default_factory=list)


class TrainTopology(BaseModel):
    train_number: str
    travel_date: str
    carriages: List[Carriage] = Field(default_factory=list)
    disconnected_carriages: List[tuple[int, int]] = Field(default_factory=list)
    restaurant_carriage_number: Optional[int] = None

    def get_carriage(self, carriage_number: int) -> Optional[Carriage]:
        for c in self.carriages:
            if c.carriage_number == carriage_number:
                return c
        return None


class TravelLeg(BaseModel):
    leg_index: int
    type: str = "SEATED"                     # "SEATED" lub "STANDING"
    seat_id: str
    carriage_number: int
    seat_number: str
    from_station: str
    to_station: str
    from_station_idx: int
    to_station_idx: int
    duration_minutes: int
    transfer_instruction_after: Optional[str] = None
    explanation: Optional[str] = None


class OptimizationProfile(str, Enum):
    BALANCED = "BALANCED"                    # Zrównoważony
    MINIMAL_WALKING = "MINIMAL_WALKING"      # Leniwy pasażer (bliskie wagony)
    MINIMAL_TRANSFERS = "MINIMAL_TRANSFERS"  # Najmniej przesiadek

