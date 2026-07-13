from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class GridPoint:
    latitude: float
    longitude: float
    h3_index: str | None = None


def haversine_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    lat_delta = radians(lat_b - lat_a)
    lon_delta = radians(lon_b - lon_a)
    lat_a_rad = radians(lat_a)
    lat_b_rad = radians(lat_b)

    haversine = sin(lat_delta / 2) ** 2 + cos(lat_a_rad) * cos(lat_b_rad) * sin(lon_delta / 2) ** 2
    return 2 * EARTH_RADIUS_KM * sqrt(haversine)


def clamp_latitude(latitude: float) -> float:
    return max(-90.0, min(90.0, latitude))


def normalize_longitude(longitude: float) -> float:
    wrapped = (longitude + 180.0) % 360.0
    return wrapped - 180.0
