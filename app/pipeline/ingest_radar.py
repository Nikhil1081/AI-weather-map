from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class RadarFrame:
    """Normalized radar payload ready for downstream inference."""

    captured_at: datetime
    source: str
    payload: dict[str, Any]


async def fetch_latest_radar_frame(source: str = "noaa") -> RadarFrame:
    """Return a placeholder frame until the upstream data connector is wired in."""

    return RadarFrame(
        captured_at=datetime.now(tz=UTC),
        source=source,
        payload={"status": "pending-ingestion", "source": source},
    )
