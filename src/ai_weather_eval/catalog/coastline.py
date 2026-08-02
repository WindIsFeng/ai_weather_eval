"""WGS84 coastline distance and crossing utilities.

The spatial index uses unit-sphere coordinates only to retrieve nearby candidate
segments.  Every reported distance, nearest point, bearing, and track
interpolation is calculated with :class:`pyproj.Geod` on the WGS84 ellipsoid.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import shapefile
from pyproj import Geod
from scipy.optimize import minimize_scalar
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point


WGS84_GEOD = Geod(ellps="WGS84")


def _unit_sphere_xyz(longitudes: np.ndarray, latitudes: np.ndarray) -> np.ndarray:
    """Return unit-sphere Cartesian coordinates for candidate lookup only."""

    lon_rad = np.deg2rad(longitudes)
    lat_rad = np.deg2rad(latitudes)
    cos_lat = np.cos(lat_rad)
    return np.column_stack(
        (cos_lat * np.cos(lon_rad), cos_lat * np.sin(lon_rad), np.sin(lat_rad))
    )


def _as_float_array(values: Sequence[float] | np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


@dataclass(frozen=True)
class CoastDistance:
    """Nearest point on a coastline segment for one query point."""

    distance_km: float
    coast_latitude: float
    coast_longitude: float
    bearing_degrees: float
    segment_id: int


@dataclass(frozen=True)
class CoastCrossing:
    """One topological crossing along a track segment."""

    track_segment_index: int
    fraction: float
    latitude: float
    longitude: float


class CoastlineIndex:
    """Candidate index and WGS84 geodesic representation of coastline segments."""

    def __init__(
        self,
        segment_longitude_a: np.ndarray,
        segment_latitude_a: np.ndarray,
        segment_longitude_b: np.ndarray,
        segment_latitude_b: np.ndarray,
        *,
        sample_spacing_km: float = 5.0,
    ) -> None:
        if sample_spacing_km <= 0:
            raise ValueError("sample_spacing_km must be positive")

        self.segment_longitude_a = _as_float_array(segment_longitude_a)
        self.segment_latitude_a = _as_float_array(segment_latitude_a)
        self.segment_longitude_b = _as_float_array(segment_longitude_b)
        self.segment_latitude_b = _as_float_array(segment_latitude_b)
        segment_shapes = {
            array.shape
            for array in (
                self.segment_longitude_a,
                self.segment_latitude_a,
                self.segment_longitude_b,
                self.segment_latitude_b,
            )
        }
        if len(segment_shapes) != 1 or self.segment_longitude_a.ndim != 1:
            raise ValueError("Coastline segment arrays must be one-dimensional and equal length")
        if not len(self.segment_longitude_a):
            raise ValueError("At least one coastline segment is required")

        azimuth, _, length_m = WGS84_GEOD.inv(
            self.segment_longitude_a,
            self.segment_latitude_a,
            self.segment_longitude_b,
            self.segment_latitude_b,
        )
        valid = np.isfinite(length_m) & (length_m > 0)
        if not valid.all():
            self.segment_longitude_a = self.segment_longitude_a[valid]
            self.segment_latitude_a = self.segment_latitude_a[valid]
            self.segment_longitude_b = self.segment_longitude_b[valid]
            self.segment_latitude_b = self.segment_latitude_b[valid]
            azimuth = np.asarray(azimuth)[valid]
            length_m = np.asarray(length_m)[valid]

        self.segment_azimuth = _as_float_array(azimuth)
        self.segment_length_m = _as_float_array(length_m)
        self.sample_spacing_km = float(sample_spacing_km)

        interval_counts = np.maximum(
            1, np.ceil(self.segment_length_m / (sample_spacing_km * 1000.0)).astype(int)
        )
        self.sample_segment_ids = np.repeat(
            np.arange(len(self.segment_length_m), dtype=np.int64), interval_counts
        )
        offsets = np.cumsum(interval_counts) - interval_counts
        local_indices = np.arange(interval_counts.sum()) - np.repeat(offsets, interval_counts)
        sample_fraction = (local_indices + 0.5) / interval_counts[self.sample_segment_ids]
        sample_distance_m = sample_fraction * self.segment_length_m[self.sample_segment_ids]
        sample_lon, sample_lat, _ = WGS84_GEOD.fwd(
            self.segment_longitude_a[self.sample_segment_ids],
            self.segment_latitude_a[self.sample_segment_ids],
            self.segment_azimuth[self.sample_segment_ids],
            sample_distance_m,
        )
        self.sample_longitudes = _as_float_array(sample_lon)
        self.sample_latitudes = _as_float_array(sample_lat)
        self._tree = cKDTree(_unit_sphere_xyz(self.sample_longitudes, self.sample_latitudes))

    @classmethod
    def from_segments(
        cls,
        segments: Iterable[tuple[float, float, float, float]],
        *,
        sample_spacing_km: float = 5.0,
    ) -> "CoastlineIndex":
        """Build an index from ``(lon_a, lat_a, lon_b, lat_b)`` segments."""

        segment_array = np.asarray(list(segments), dtype=np.float64)
        if segment_array.ndim != 2 or segment_array.shape[1] != 4:
            raise ValueError("segments must contain (lon_a, lat_a, lon_b, lat_b) rows")
        return cls(
            segment_array[:, 0],
            segment_array[:, 1],
            segment_array[:, 2],
            segment_array[:, 3],
            sample_spacing_km=sample_spacing_km,
        )

    @classmethod
    def from_gshhg(
        cls,
        shapefile_path: str | Path,
        *,
        sample_spacing_km: float = 5.0,
        minimum_polygon_area_km2: float = 0.0,
    ) -> "CoastlineIndex":
        """Load GSHHG level-1 polygons as ocean-land boundary segments."""

        path = Path(shapefile_path)
        if not path.is_file():
            raise FileNotFoundError(f"GSHHG shapefile does not exist: {path}")
        if minimum_polygon_area_km2 < 0:
            raise ValueError("minimum_polygon_area_km2 cannot be negative")

        longitude_a: list[float] = []
        latitude_a: list[float] = []
        longitude_b: list[float] = []
        latitude_b: list[float] = []
        with shapefile.Reader(str(path)) as reader:
            field_names = [field.name for field in reader.fields[1:]]
            area_index = field_names.index("area") if "area" in field_names else None
            for shape_record in reader.iterShapeRecords():
                if area_index is not None:
                    area_km2 = float(shape_record.record[area_index])
                    if area_km2 < minimum_polygon_area_km2:
                        continue
                shape = shape_record.shape
                part_starts = list(shape.parts) + [len(shape.points)]
                for start, stop in zip(part_starts[:-1], part_starts[1:], strict=True):
                    ring = shape.points[start:stop]
                    if len(ring) < 2:
                        continue
                    if ring[0] != ring[-1]:
                        ring = [*ring, ring[0]]
                    for point_a, point_b in zip(ring[:-1], ring[1:], strict=True):
                        longitude_a.append(float(point_a[0]))
                        latitude_a.append(float(point_a[1]))
                        longitude_b.append(float(point_b[0]))
                        latitude_b.append(float(point_b[1]))

        return cls(
            np.asarray(longitude_a),
            np.asarray(latitude_a),
            np.asarray(longitude_b),
            np.asarray(latitude_b),
            sample_spacing_km=sample_spacing_km,
        )

    def _candidate_segment_ids(self, latitude: float, longitude: float, k: int) -> np.ndarray:
        query_xyz = _unit_sphere_xyz(np.asarray([longitude]), np.asarray([latitude]))[0]
        candidate_count = min(max(1, k), len(self.sample_segment_ids))
        _, sample_indices = self._tree.query(query_xyz, k=candidate_count)
        sample_indices = np.atleast_1d(sample_indices)
        return np.unique(self.sample_segment_ids[sample_indices])

    def _point_on_segment(self, segment_id: int, distance_m: float) -> tuple[float, float]:
        lon, lat, _ = WGS84_GEOD.fwd(
            self.segment_longitude_a[segment_id],
            self.segment_latitude_a[segment_id],
            self.segment_azimuth[segment_id],
            distance_m,
        )
        return float(lon), float(lat)

    def _distance_to_segment(
        self, latitude: float, longitude: float, segment_id: int
    ) -> tuple[float, float, float]:
        length_m = float(self.segment_length_m[segment_id])

        def objective(distance_along_m: float) -> float:
            coast_lon, coast_lat = self._point_on_segment(segment_id, distance_along_m)
            _, _, distance_m = WGS84_GEOD.inv(
                longitude, latitude, coast_lon, coast_lat
            )
            return abs(float(distance_m))

        optimum = minimize_scalar(
            objective,
            bounds=(0.0, length_m),
            method="bounded",
            options={"xatol": 0.1},
        )
        candidates = ((0.0, objective(0.0)), (length_m, objective(length_m)))
        best_distance_along_m, best_distance_m = min(
            (*candidates, (float(optimum.x), float(optimum.fun))), key=lambda item: item[1]
        )
        coast_lon, coast_lat = self._point_on_segment(segment_id, best_distance_along_m)
        return best_distance_m, coast_lat, coast_lon

    def distance_to_point(
        self, latitude: float, longitude: float, *, candidate_samples: int = 12
    ) -> CoastDistance:
        """Calculate the exact WGS84 distance to nearby candidate geodesic segments."""

        if not np.isfinite(latitude) or not np.isfinite(longitude):
            raise ValueError("latitude and longitude must be finite")
        candidate_ids = self._candidate_segment_ids(latitude, longitude, candidate_samples)
        best: tuple[float, float, float, int] | None = None
        for segment_id in candidate_ids:
            distance_m, coast_lat, coast_lon = self._distance_to_segment(
                latitude, longitude, int(segment_id)
            )
            candidate = (distance_m, coast_lat, coast_lon, int(segment_id))
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is None:  # pragma: no cover - construction prevents an empty index
            raise RuntimeError("No coastline segment candidates were found")
        distance_m, coast_lat, coast_lon, segment_id = best
        bearing, _, _ = WGS84_GEOD.inv(longitude, latitude, coast_lon, coast_lat)
        return CoastDistance(
            distance_km=distance_m / 1000.0,
            coast_latitude=coast_lat,
            coast_longitude=coast_lon,
            bearing_degrees=float(bearing) % 360.0,
            segment_id=segment_id,
        )

    def distances_to_points(
        self,
        latitudes: Sequence[float] | np.ndarray,
        longitudes: Sequence[float] | np.ndarray,
        *,
        candidate_samples: int = 12,
    ) -> list[CoastDistance]:
        """Calculate WGS84 coastline distances for equally sized coordinate arrays."""

        latitudes_array = _as_float_array(latitudes)
        longitudes_array = _as_float_array(longitudes)
        if latitudes_array.shape != longitudes_array.shape:
            raise ValueError("latitudes and longitudes must have equal shape")
        return [
            self.distance_to_point(lat, lon, candidate_samples=candidate_samples)
            for lat, lon in zip(latitudes_array, longitudes_array, strict=True)
        ]

    @staticmethod
    def _local_xy(
        center_latitude: float,
        center_longitude: float,
        latitudes: Sequence[float],
        longitudes: Sequence[float],
    ) -> np.ndarray:
        azimuth, _, distance_m = WGS84_GEOD.inv(
            np.full(len(latitudes), center_longitude),
            np.full(len(latitudes), center_latitude),
            np.asarray(longitudes),
            np.asarray(latitudes),
        )
        azimuth_rad = np.deg2rad(azimuth)
        return np.column_stack(
            (np.asarray(distance_m) * np.sin(azimuth_rad), np.asarray(distance_m) * np.cos(azimuth_rad))
        )

    def find_crossings(
        self,
        latitudes: Sequence[float] | np.ndarray,
        longitudes: Sequence[float] | np.ndarray,
        *,
        maximum_subsegment_km: float = 20.0,
        candidate_samples: int = 16,
    ) -> list[CoastCrossing]:
        """Find track/coastline intersections using local WGS84 geodesic geometry."""

        if maximum_subsegment_km <= 0:
            raise ValueError("maximum_subsegment_km must be positive")
        latitudes_array = _as_float_array(latitudes)
        longitudes_array = _as_float_array(longitudes)
        if latitudes_array.shape != longitudes_array.shape:
            raise ValueError("latitudes and longitudes must have equal shape")
        if len(latitudes_array) < 2:
            return []

        crossings: list[CoastCrossing] = []
        for track_segment_index in range(len(latitudes_array) - 1):
            lon_a = float(longitudes_array[track_segment_index])
            lat_a = float(latitudes_array[track_segment_index])
            lon_b = float(longitudes_array[track_segment_index + 1])
            lat_b = float(latitudes_array[track_segment_index + 1])
            track_azimuth, _, track_length_m = WGS84_GEOD.inv(lon_a, lat_a, lon_b, lat_b)
            subsegment_count = max(1, ceil(track_length_m / (maximum_subsegment_km * 1000.0)))
            for subsegment_index in range(subsegment_count):
                start_distance_m = track_length_m * subsegment_index / subsegment_count
                end_distance_m = track_length_m * (subsegment_index + 1) / subsegment_count
                middle_distance_m = (start_distance_m + end_distance_m) / 2.0
                sub_lon_a, sub_lat_a, _ = WGS84_GEOD.fwd(
                    lon_a, lat_a, track_azimuth, start_distance_m
                )
                sub_lon_b, sub_lat_b, _ = WGS84_GEOD.fwd(
                    lon_a, lat_a, track_azimuth, end_distance_m
                )
                center_lon, center_lat, _ = WGS84_GEOD.fwd(
                    lon_a, lat_a, track_azimuth, middle_distance_m
                )
                candidate_ids = self._candidate_segment_ids(
                    center_lat, center_lon, candidate_samples
                )
                track_xy = self._local_xy(
                    center_lat,
                    center_lon,
                    [sub_lat_a, sub_lat_b],
                    [sub_lon_a, sub_lon_b],
                )
                track_line = LineString(track_xy)
                for segment_id in candidate_ids:
                    segment_id = int(segment_id)
                    coast_xy = self._local_xy(
                        center_lat,
                        center_lon,
                        [
                            self.segment_latitude_a[segment_id],
                            self.segment_latitude_b[segment_id],
                        ],
                        [
                            self.segment_longitude_a[segment_id],
                            self.segment_longitude_b[segment_id],
                        ],
                    )
                    intersection = track_line.intersection(LineString(coast_xy))
                    if intersection.is_empty:
                        continue
                    if isinstance(intersection, Point):
                        intersection_point = intersection
                    else:
                        intersection_points = [
                            geometry
                            for geometry in getattr(intersection, "geoms", ())
                            if isinstance(geometry, Point)
                        ]
                        if not intersection_points:
                            continue
                        intersection_point = intersection_points[0]
                    local_fraction = track_line.project(intersection_point, normalized=True)
                    fraction = (subsegment_index + local_fraction) / subsegment_count
                    crossing_lon, crossing_lat, _ = WGS84_GEOD.fwd(
                        lon_a, lat_a, track_azimuth, track_length_m * fraction
                    )
                    crossings.append(
                        CoastCrossing(
                            track_segment_index=track_segment_index,
                            fraction=float(fraction),
                            latitude=float(crossing_lat),
                            longitude=float(crossing_lon),
                        )
                    )
                    break

        deduplicated: list[CoastCrossing] = []
        for crossing in crossings:
            if deduplicated:
                previous = deduplicated[-1]
                _, _, separation_m = WGS84_GEOD.inv(
                    previous.longitude,
                    previous.latitude,
                    crossing.longitude,
                    crossing.latitude,
                )
                if crossing.track_segment_index == previous.track_segment_index and separation_m < 1000.0:
                    midpoint_azimuth, _, _ = WGS84_GEOD.inv(
                        previous.longitude,
                        previous.latitude,
                        crossing.longitude,
                        crossing.latitude,
                    )
                    midpoint_lon, midpoint_lat, _ = WGS84_GEOD.fwd(
                        previous.longitude,
                        previous.latitude,
                        midpoint_azimuth,
                        separation_m / 2.0,
                    )
                    deduplicated[-1] = CoastCrossing(
                        track_segment_index=previous.track_segment_index,
                        fraction=(previous.fraction + crossing.fraction) / 2.0,
                        latitude=float(midpoint_lat),
                        longitude=float(midpoint_lon),
                    )
                    continue
            deduplicated.append(crossing)
        return deduplicated
