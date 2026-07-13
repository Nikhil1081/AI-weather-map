from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.pipeline.ingest_radar import RadarFrame


@dataclass(frozen=True)
class WeatherDetection:
    label: str
    confidence: float
    severity: str
    metadata: dict[str, Any]


class AIDetector:
    """Model wrapper for radar and satellite frame inference."""

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path

    async def detect(self, frame: RadarFrame) -> list[WeatherDetection]:
        """Return deterministic placeholder detections until a trained model is attached."""

        _ = frame
        return [
            WeatherDetection(
                label="clear_sky",
                confidence=0.0,
                severity="info",
                metadata={"model_path": self.model_path, "status": "untrained"},
            )
        ]
