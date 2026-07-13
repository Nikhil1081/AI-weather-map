from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.core.telemetry import GridPoint, clamp_latitude, normalize_longitude

try:
    import h3
except ImportError:  # pragma: no cover - dependency may not be installed locally yet
    h3 = None

try:
    from shapely.geometry import Point, Polygon
except ImportError:  # pragma: no cover - dependency may not be installed locally yet
    Point = None
    Polygon = None


@dataclass(frozen=True)
class GridCell:
    h3_index: str
    latitude: float
    longitude: float
    properties: dict[str, Any]


class VectorGridIndexer:
    """Translate geographic coordinates into spatial cell identifiers."""

    def __init__(self, resolution: int = 7) -> None:
        self.resolution = resolution

    def index_point(self, latitude: float, longitude: float) -> GridPoint:
        normalized_latitude = clamp_latitude(latitude)
        normalized_longitude = normalize_longitude(longitude)

        h3_index: str | None = None
        if h3 is not None:
            h3_index = h3.geo_to_h3(normalized_latitude, normalized_longitude, self.resolution)

        return GridPoint(
            latitude=normalized_latitude,
            longitude=normalized_longitude,
            h3_index=h3_index,
        )

    def build_cell(self, latitude: float, longitude: float, **properties: Any) -> GridCell:
        point = self.index_point(latitude, longitude)
        return GridCell(
            h3_index=point.h3_index or f"grid-{self.resolution}:{point.latitude:.4f}:{point.longitude:.4f}",
            latitude=point.latitude,
            longitude=point.longitude,
            properties=properties,
        )

    def point_to_cell(self, latitude: float, longitude: float, **properties: Any) -> GridCell:
        return self.build_cell(latitude, longitude, **properties)

    def batch_index(self, coordinates: Iterable[tuple[float, float]]) -> list[GridCell]:
        return [self.build_cell(latitude, longitude) for latitude, longitude in coordinates]

    def cell_boundary(self, cell: GridCell) -> list[list[float]]:
        if h3 is None or cell.h3_index.startswith("grid-"):
            half_step = 0.01
            return [
                [cell.longitude - half_step, cell.latitude - half_step],
                [cell.longitude + half_step, cell.latitude - half_step],
                [cell.longitude + half_step, cell.latitude + half_step],
                [cell.longitude - half_step, cell.latitude + half_step],
                [cell.longitude - half_step, cell.latitude - half_step],
            ]

        boundary = h3.h3_to_geo_boundary(cell.h3_index, geo_json=True)
        coordinates = [[longitude, latitude] for latitude, longitude in boundary]
        coordinates.append(coordinates[0])
        return coordinates

    def cell_polygon(self, cell: GridCell):
        boundary = self.cell_boundary(cell)
        if Polygon is None:
            return boundary
        return Polygon(boundary)

    def contains_point(self, cell: GridCell, latitude: float, longitude: float) -> bool:
        if Point is None or Polygon is None:
            return abs(cell.latitude - latitude) <= 0.01 and abs(cell.longitude - longitude) <= 0.01

        polygon = self.cell_polygon(cell)
        if not isinstance(polygon, Polygon):
            return False
        return polygon.contains(Point(longitude, latitude))

    def to_feature(self, cell: GridCell) -> dict[str, Any]:
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [cell.longitude, cell.latitude],
            },
            "properties": {
                "h3_index": cell.h3_index,
                **cell.properties,
            },
        }
