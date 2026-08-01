"""Stable records exchanged between catalog, verification, and reporting stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


CANONICAL_FIELD_VARIABLES = (
    "msl",
    "u10",
    "v10",
    "z500",
    "u850",
    "v850",
    "u200",
    "v200",
)


@dataclass(frozen=True)
class LandfallCase:
    """One observed ocean-to-land crossing used as an evaluation event."""

    case_id: str
    storm_id: str
    landfall_index: int
    basin: str
    landfall_time: datetime
    latitude: float
    longitude: float
    lifetime_vmax_kt: float
    landfall_vmax_kt: Optional[float] = None
    landfall_mslp_hpa: Optional[float] = None
    qc_status: str = "pending"


@dataclass(frozen=True)
class MetricRecord:
    """One tidy verification value."""

    case_id: str
    storm_id: str
    model: str
    init_time: datetime
    nominal_lead_hours: int
    valid_time: datetime
    metric: str
    value: float
    unit: str
    variable: Optional[str] = None
    domain: Optional[str] = None
    qc_flag: str = "ok"

