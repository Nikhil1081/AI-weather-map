# AI Weather Map

Async-first Python service for weather ingestion, detection, geospatial indexing, and streaming GeoJSON delivery.

## Layout

- `app/main.py` exposes the FastAPI app and NDJSON weather stream.
- `app/pipeline/` contains radar ingestion and model inference hooks.
- `app/services/` contains grid indexing and routing helpers.
- `app/core/` contains settings and coordinate utilities.

## Run

Install dependencies, then start the API with Uvicorn:

```bash
uvicorn app.main:app --reload
```

The stream endpoint is available at `GET /v1/weather/stream`.
The route scoring endpoint is available at `POST /v1/routing/score`.

## Web App

Open `http://127.0.0.1:8000/` to use the single-page dashboard.

- Type a city, region, or country to center the Google map on that location.
- The weather panel will summarize the location-based weather signal.
- The chatbot panel uses Groq only for replies.

Additional endpoints:

- `POST /v1/weather/location` for geocoding and weather analysis.
- `POST /v1/chat/respond` for chatbot replies.

## Required Keys

- `AI_WEATHER_GOOGLE_MAPS_API_KEY` to load the Google Maps view.
- `AI_WEATHER_GROQ_API_KEY` to enable chatbot replies.
