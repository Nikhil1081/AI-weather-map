from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.vector_grid import GridCell


@dataclass(frozen=True)
class RouteAlert:
    route_id: str
    severity: str
    message: str
    metadata: dict[str, Any]


class AlertEngine:
    """Assign risk to route segments that overlap active weather cells."""

    def route_risk_score(self, cells: list[GridCell]) -> float:
        score = 0.0
        for cell in cells:
            severity = str(cell.properties.get("severity", "info"))
            confidence = float(cell.properties.get("confidence", 0.0))
            weight = 0.0
            if severity == "critical":
                weight = 3.0
            elif severity == "warning":
                weight = 1.5
            elif severity == "advisory":
                weight = 0.75
            score += weight * max(0.0, min(1.0, confidence or 0.5))
        return score

    def score_route(self, route_id: str, cells: list[GridCell]) -> RouteAlert:
        severe_hits = sum(1 for cell in cells if cell.properties.get("severity") in {"warning", "critical"})
        risk_score = self.route_risk_score(cells)
        if risk_score >= 5.0 or severe_hits >= 3:
            severity = "critical"
            message = "Route intersects multiple severe-weather cells"
        elif risk_score >= 1.5 or severe_hits:
            severity = "warning"
            message = "Route intersects weather-risk cells"
        else:
            severity = "info"
            message = "Route clear"
        return RouteAlert(
            route_id=route_id,
            severity=severity,
            message=message,
            metadata={"cell_count": len(cells), "risk_score": round(risk_score, 3)},
        )
