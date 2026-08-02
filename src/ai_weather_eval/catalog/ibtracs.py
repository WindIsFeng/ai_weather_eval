"""IBTrACS CSV ingestion and quality checks for case selection."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd


WIND_RADIUS_COLUMNS = tuple(
    f"USA_R{threshold}_{quadrant}"
    for threshold in (34, 50, 64)
    for quadrant in ("NE", "SE", "SW", "NW")
)

NUMERIC_COLUMNS = (
    "LAT",
    "LON",
    "WMO_WIND",
    "WMO_PRES",
    "USA_LAT",
    "USA_LON",
    "USA_WIND",
    "USA_PRES",
    *WIND_RADIUS_COLUMNS,
)

REQUIRED_COLUMNS = (
    "SID",
    "BASIN",
    "NAME",
    "ISO_TIME",
    "NATURE",
    "LAT",
    "LON",
    "TRACK_TYPE",
    "USA_STATUS",
    "USA_WIND",
    *WIND_RADIUS_COLUMNS,
)


class IBTrACSDataError(ValueError):
    """Raised when an IBTrACS source cannot support reproducible selection."""


def load_ibtracs_csv(path: str | Path) -> pd.DataFrame:
    """Load an IBTrACS list CSV while preserving the North Atlantic ``NA`` code."""

    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(f"IBTrACS CSV does not exist: {source_path}")

    frame = pd.read_csv(
        source_path,
        skiprows=[1],  # IBTrACS puts a units row directly below the header.
        low_memory=False,
        skipinitialspace=True,
        keep_default_na=False,
        na_values=[""],
    )
    missing = sorted(set(REQUIRED_COLUMNS).difference(frame.columns))
    if missing:
        raise IBTrACSDataError("Missing required IBTrACS columns: " + ", ".join(missing))

    string_columns = frame.select_dtypes(include="object").columns
    frame[string_columns] = frame[string_columns].apply(lambda column: column.str.strip())
    frame["ISO_TIME"] = pd.to_datetime(frame["ISO_TIME"], errors="coerce", utc=True)
    numeric_columns = [column for column in NUMERIC_COLUMNS if column in frame]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame = frame.copy()
    frame = frame.assign(
        TRACK_TYPE_NORMALIZED=frame["TRACK_TYPE"].str.lower(),
        SOURCE_ROW=range(len(frame)),
    )

    if frame["SID"].isna().any():
        raise IBTrACSDataError("IBTrACS contains rows with missing SID")
    if frame["ISO_TIME"].isna().any():
        count = int(frame["ISO_TIME"].isna().sum())
        raise IBTrACSDataError(f"IBTrACS contains {count} rows with invalid ISO_TIME")
    duplicate_count = int(frame.duplicated(["SID", "ISO_TIME"]).sum())
    if duplicate_count:
        raise IBTrACSDataError(
            f"IBTrACS contains {duplicate_count} duplicate (SID, ISO_TIME) rows"
        )
    if frame["LAT"].isna().any() or frame["LON"].isna().any():
        raise IBTrACSDataError("IBTrACS mean track contains missing LAT/LON values")

    return frame.sort_values(["SID", "ISO_TIME"], kind="stable").reset_index(drop=True)


def select_track_types(frame: pd.DataFrame, accepted_types: Iterable[str]) -> pd.DataFrame:
    """Return a copy restricted to normalized track types."""

    normalized = {track_type.lower() for track_type in accepted_types}
    selected = frame[frame["TRACK_TYPE_NORMALIZED"].isin(normalized)].copy()
    if selected.empty:
        raise IBTrACSDataError(
            "No IBTrACS rows matched track types: " + ", ".join(sorted(normalized))
        )
    return selected


def ibtracs_quality_summary(frame: pd.DataFrame) -> dict[str, object]:
    """Return compact source-quality evidence used in catalog manifests."""

    radii = list(WIND_RADIUS_COLUMNS)
    return {
        "row_count": int(len(frame)),
        "storm_count": int(frame["SID"].nunique()),
        "time_start": frame["ISO_TIME"].min().isoformat(),
        "time_end": frame["ISO_TIME"].max().isoformat(),
        "track_type_rows": {
            str(key): int(value) for key, value in frame["TRACK_TYPE"].value_counts().items()
        },
        "usa_wind_missing_fraction": float(frame["USA_WIND"].isna().mean()),
        "usa_position_missing_fraction": float(
            (frame["USA_LAT"].isna() | frame["USA_LON"].isna()).mean()
        ),
        "any_usa_wind_radius_fraction": float(frame[radii].notna().any(axis=1).mean()),
        "duplicate_sid_time_count": int(frame.duplicated(["SID", "ISO_TIME"]).sum()),
    }
