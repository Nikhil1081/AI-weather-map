# Copilot Instructions

## Project Summary
- Pure-Python FastAPI weather service scaffold.
- Main API entrypoint: app/main.py.
- Stream endpoint: GET /v1/weather/stream.
- Route scoring endpoint: POST /v1/routing/score.

## Current Structure
- app/core/config.py for settings.
- app/core/telemetry.py for coordinate helpers.
- app/pipeline/ingest_radar.py for async ingestion.
- app/pipeline/ai_detector.py for inference hooks.
- app/services/vector_grid.py for H3/grid helpers.
- app/services/alert_engine.py for route risk scoring.

## Notes
- Keep changes focused and async-first.
- Preserve the existing Python-only architecture.
- README.md contains the basic run command.
