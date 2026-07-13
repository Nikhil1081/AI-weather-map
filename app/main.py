from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import AsyncIterator
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.pipeline.ai_detector import AIDetector
from app.pipeline.ingest_radar import fetch_latest_radar_frame
from app.services.alert_engine import AlertEngine
from app.services.vector_grid import VectorGridIndexer

settings = get_settings()
app = FastAPI(title=settings.app_name, version=settings.app_version)


LOCAL_PLACE_ALIASES: dict[str, tuple[str, float, float]] = {
  "dallas": ("Dallas, Texas, United States", 32.7767, -96.7970),
  "miami": ("Miami, Florida, United States", 25.7617, -80.1918),
  "new york": ("New York, New York, United States", 40.7128, -74.0060),
  "chicago": ("Chicago, Illinois, United States", 41.8781, -87.6298),
  "los angeles": ("Los Angeles, California, United States", 34.0522, -118.2437),
  "london": ("London, England, United Kingdom", 51.5072, -0.1276),
  "paris": ("Paris, Île-de-France, France", 48.8566, 2.3522),
  "tokyo": ("Tokyo, Japan", 35.6762, 139.6503),
  "sydney": ("Sydney, New South Wales, Australia", -33.8688, 151.2093),
  "toronto": ("Toronto, Ontario, Canada", 43.6532, -79.3832),
  "brazil": ("Brasília, Federal District, Brazil", -15.7939, -47.8828),
  "india": ("New Delhi, Delhi, India", 28.6139, 77.2090),
}


class GeoJSONGeometry(BaseModel):
    type: str = Field(default="Point")
    coordinates: list[float]


class GeoJSONFeature(BaseModel):
    type: str = Field(default="Feature")
    geometry: GeoJSONGeometry
    properties: dict[str, object]


class GeoJSONFeatureCollection(BaseModel):
    type: str = Field(default="FeatureCollection")
    features: list[GeoJSONFeature]


class RouteRequest(BaseModel):
    route_id: str
    coordinates: list[list[float]]


class LocationQuery(BaseModel):
    query: str


class ChatRequest(BaseModel):
    message: str
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class LocationResponse(BaseModel):
    query: str
    display_name: str
    latitude: float
    longitude: float
    h3_index: str
    weather_summary: str
    weather_severity: str
    route_alert: dict[str, object]


class SearchCandidate(BaseModel):
    display_name: str
    latitude: float
    longitude: float
    name: str | None = None
    state: str | None = None
    country: str | None = None
    osm_value: str | None = None



def call_weather_api(path: str, params: dict[str, str]) -> dict[str, object] | list[dict[str, object]] | None:
  if not settings.weather_api_key:
    return None

  query_params = {"key": settings.weather_api_key, **params}
  request_url = f"{settings.weather_api_base_url.rstrip('/')}" + path + f"?{urlencode(query_params)}"
  request = Request(request_url, headers={"User-Agent": "ai-weather-map/1.0"})

  try:
    with urlopen(request, timeout=12) as response:
      return json.loads(response.read().decode("utf-8"))
  except (URLError, TimeoutError, ValueError, OSError):
    return None


def summarize_weather(weather: dict[str, object]) -> tuple[str, str, float]:
  current = weather.get("current", {}) if isinstance(weather, dict) else {}
  condition = current.get("condition", {}) if isinstance(current, dict) else {}
  condition_text = str(condition.get("text", "Unknown conditions"))
  temp_c = current.get("temp_c")
  feelslike_c = current.get("feelslike_c")
  wind_kph = float(current.get("wind_kph", 0.0) or 0.0)
  precip_mm = float(current.get("precip_mm", 0.0) or 0.0)

  severity = "info"
  confidence = 0.6
  if precip_mm >= 2 or wind_kph >= 40:
    severity = "warning"
    confidence = 0.8
  if precip_mm >= 8 or wind_kph >= 65 or any(term in condition_text.lower() for term in ["thunder", "storm", "torrential", "blizzard"]):
    severity = "critical"
    confidence = 0.92

  if temp_c is not None and feelslike_c is not None:
    summary = f"{condition_text}. Temperature is {float(temp_c):.1f}°C and feels like {float(feelslike_c):.1f}°C."
  elif temp_c is not None:
    summary = f"{condition_text}. Temperature is {float(temp_c):.1f}°C."
  else:
    summary = condition_text

  if precip_mm:
    summary += f" Precipitation is {precip_mm:.1f} mm."
  if wind_kph:
    summary += f" Wind is {wind_kph:.0f} kph."

  return summary, severity, confidence


WMO_CODES: dict[int, tuple[str, str, float]] = {
    0: ("Clear sky", "info", 0.9),
    1: ("Mainly clear", "info", 0.8),
    2: ("Partly cloudy", "info", 0.7),
    3: ("Overcast", "info", 0.6),
    45: ("Foggy", "warning", 0.7),
    48: ("Depositing rime fog", "warning", 0.6),
    51: ("Light drizzle", "info", 0.7),
    53: ("Moderate drizzle", "info", 0.7),
    55: ("Dense drizzle", "info", 0.7),
    61: ("Slight rain", "info", 0.8),
    63: ("Moderate rain", "warning", 0.85),
    65: ("Heavy rain", "warning", 0.9),
    71: ("Slight snow fall", "warning", 0.8),
    73: ("Moderate snow fall", "warning", 0.85),
    75: ("Heavy snow fall", "critical", 0.9),
    80: ("Slight rain showers", "info", 0.8),
    81: ("Moderate rain showers", "warning", 0.85),
    82: ("Violent rain showers", "critical", 0.9),
    95: ("Thunderstorm", "critical", 0.95),
    96: ("Thunderstorm with slight hail", "critical", 0.95),
    99: ("Thunderstorm with heavy hail", "critical", 0.98),
}


def call_photon_api(query: str) -> list[dict[str, object]]:
    params = {"q": query, "limit": "10"}
    request_url = f"https://photon.komoot.io/api/?{urlencode(params)}"
    request = Request(request_url, headers={"User-Agent": "ai-weather-map/1.0"})
    try:
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
            features = data.get("features", [])
            candidates = []
            for f in features:
                props = f.get("properties", {})
                geometry = f.get("geometry", {})
                coordinates = geometry.get("coordinates", [])
                if len(coordinates) < 2:
                    continue
                # Photon coordinates are [lon, lat]
                lon = float(coordinates[0])
                lat = float(coordinates[1])
                
                name = props.get("name", "")
                city = props.get("city") or props.get("town") or props.get("district") or props.get("locality")
                state = props.get("state") or props.get("region")
                country = props.get("country", "")
                
                parts = []
                if city and str(city).lower() != str(name).lower():
                    parts.append(str(city))
                if state and str(state).lower() != str(name).lower() and (not city or str(state).lower() != str(city).lower()):
                    parts.append(str(state))
                if country:
                    parts.append(str(country))
                
                secondary = ", ".join(parts)
                display_name = f"{name}, {secondary}" if secondary else name
                
                candidates.append({
                    "display_name": display_name,
                    "latitude": lat,
                    "longitude": lon,
                    "name": name,
                    "state": state,
                    "country": country,
                    "osm_value": props.get("osm_value")
                })
            return candidates
    except Exception:
        return []


def call_nominatim(query: str) -> dict[str, object] | None:
    params = {"q": query, "format": "json", "limit": "1", "addressdetails": "1"}
    request_url = f"https://nominatim.openstreetmap.org/search?{urlencode(params)}"
    request = Request(request_url, headers={"User-Agent": "ai-weather-map/1.0 (nikhil@example.com)"})
    try:
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data and isinstance(data, list):
                return data[0]
    except Exception:
        pass
    return None


def call_open_meteo_api(lat: float, lon: float) -> dict[str, object] | None:
    params = {
        "latitude": str(lat),
        "longitude": str(lon),
        "current": "temperature_2m,apparent_temperature,wind_speed_10m,precipitation,weather_code"
    }
    request_url = f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"
    request = Request(request_url, headers={"User-Agent": "ai-weather-map/1.0"})
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def parse_open_meteo_weather(data: dict[str, object]) -> tuple[str, str, float]:
    current = data.get("current", {}) if isinstance(data, dict) else {}
    temp_c = current.get("temperature_2m")
    feelslike_c = current.get("apparent_temperature")
    wind_kph = current.get("wind_speed_10m")
    precip_mm = current.get("precipitation")
    wmo_code = current.get("weather_code", 0)

    condition_text, severity, confidence = WMO_CODES.get(wmo_code, ("Unknown conditions", "info", 0.5))

    if temp_c is not None and feelslike_c is not None:
        summary = f"{condition_text}. Temperature is {float(temp_c):.1f}°C and feels like {float(feelslike_c):.1f}°C."
    elif temp_c is not None:
        summary = f"{condition_text}. Temperature is {float(temp_c):.1f}°C."
    else:
        summary = condition_text

    if precip_mm:
        summary += f" Precipitation is {float(precip_mm):.1f} mm."
    if wind_kph:
        summary += f" Wind is {float(wind_kph):.0f} kph."

    return summary, severity, confidence


def geocode_location(query: str) -> dict[str, object] | None:
  normalized_query = query.strip().lower()

  # 1. Match coordinates directly
  coordinate_match = re.match(r"^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$", query.strip())
  if coordinate_match:
    lat = float(coordinate_match.group(1))
    lon = float(coordinate_match.group(2))
    
    weather_summary, weather_severity, confidence = "Unknown weather", "info", 0.5
    display_name = f"Coordinates: {lat}, {lon}"
    
    if settings.weather_api_key:
      weather_data = call_weather_api("/v1/current.json", {"q": query.strip()})
      if isinstance(weather_data, dict):
        location = weather_data.get("location", {})
        display_name = ", ".join(
          part for part in [str(location.get("name", "")), str(location.get("region", "")), str(location.get("country", ""))] if part
        ) or display_name
        weather_summary, weather_severity, confidence = summarize_weather(weather_data)
        return {
          "display_name": display_name,
          "latitude": lat,
          "longitude": lon,
          "weather_summary": weather_summary,
          "weather_severity": weather_severity,
          "confidence": confidence,
        }
    
    # Fallback to Open-Meteo
    meteo_data = call_open_meteo_api(lat, lon)
    if meteo_data:
      weather_summary, weather_severity, confidence = parse_open_meteo_weather(meteo_data)
      
    return {
      "display_name": display_name,
      "latitude": lat,
      "longitude": lon,
      "weather_summary": weather_summary,
      "weather_severity": weather_severity,
      "confidence": confidence,
    }

  # 2. Search using Photon API first (supports all small cities, towns, villages worldwide)
  photon_results = call_photon_api(query)
  if photon_results:
    first = photon_results[0]
    lat = first["latitude"]
    lon = first["longitude"]
    display_name = first["display_name"]
    
    weather_summary, weather_severity, confidence = "Unknown weather", "info", 0.5
    
    if settings.weather_api_key:
      weather_data = call_weather_api("/v1/current.json", {"q": f"{lat},{lon}"})
      if isinstance(weather_data, dict):
        weather_summary, weather_severity, confidence = summarize_weather(weather_data)
        return {
          "display_name": display_name,
          "latitude": lat,
          "longitude": lon,
          "weather_summary": weather_summary,
          "weather_severity": weather_severity,
          "confidence": confidence,
        }
        
    meteo_data = call_open_meteo_api(lat, lon)
    if meteo_data:
      weather_summary, weather_severity, confidence = parse_open_meteo_weather(meteo_data)
      
    return {
      "display_name": display_name,
      "latitude": lat,
      "longitude": lon,
      "weather_summary": weather_summary,
      "weather_severity": weather_severity,
      "confidence": confidence,
    }

  # 3. Search using Nominatim OpenStreetMap API as fallback
  geo_result = call_nominatim(query)
  if geo_result:
    lat = float(geo_result["lat"])
    lon = float(geo_result["lon"])
    display_name = geo_result.get("display_name", query)
    
    weather_summary, weather_severity, confidence = "Unknown weather", "info", 0.5
    
    if settings.weather_api_key:
      weather_data = call_weather_api("/v1/current.json", {"q": f"{lat},{lon}"})
      if isinstance(weather_data, dict):
        weather_summary, weather_severity, confidence = summarize_weather(weather_data)
        return {
          "display_name": display_name,
          "latitude": lat,
          "longitude": lon,
          "weather_summary": weather_summary,
          "weather_severity": weather_severity,
          "confidence": confidence,
        }
        
    meteo_data = call_open_meteo_api(lat, lon)
    if meteo_data:
      weather_summary, weather_severity, confidence = parse_open_meteo_weather(meteo_data)
      
    return {
      "display_name": display_name,
      "latitude": lat,
      "longitude": lon,
      "weather_summary": weather_summary,
      "weather_severity": weather_severity,
      "confidence": confidence,
    }

  # 3. Local place aliases fallback
  if normalized_query in LOCAL_PLACE_ALIASES:
    display_name, latitude, longitude = LOCAL_PLACE_ALIASES[normalized_query]
    
    weather_summary, weather_severity, confidence = "Unknown weather", "info", 0.5
    if settings.weather_api_key:
      weather_data = call_weather_api("/v1/current.json", {"q": f"{latitude},{longitude}"})
      if isinstance(weather_data, dict):
        weather_summary, weather_severity, confidence = summarize_weather(weather_data)
        return {
          "display_name": display_name,
          "latitude": latitude,
          "longitude": longitude,
          "weather_summary": weather_summary,
          "weather_severity": weather_severity,
          "confidence": confidence,
        }
        
    meteo_data = call_open_meteo_api(latitude, longitude)
    if meteo_data:
      weather_summary, weather_severity, confidence = parse_open_meteo_weather(meteo_data)
      
    return {
      "display_name": display_name,
      "latitude": latitude,
      "longitude": longitude,
      "weather_summary": weather_summary,
      "weather_severity": weather_severity,
      "confidence": confidence,
    }

  return None


def infer_weather_context(latitude: float, longitude: float) -> tuple[str, str, float]:
    current_month = datetime.now(tz=timezone.utc).month
    absolute_latitude = abs(latitude)

    if absolute_latitude <= 23.5:
        summary = "Tropical weather band with humidity, fast-moving showers, and thunderstorm bursts."
        severity = "warning"
        confidence = 0.72
    elif absolute_latitude <= 45:
        summary = "Temperate zone with shifting fronts and periodic convection potential."
        severity = "info"
        confidence = 0.54
    else:
        summary = "Higher-latitude air mass with calmer conditions but sharper frontal changes."
        severity = "info"
        confidence = 0.48

    if latitude >= 35 and current_month in {5, 6, 7, 8, 9}:
        summary = "Summer convection and localized storm risk are more likely in this region right now."
        severity = "warning"
        confidence = max(confidence, 0.7)
    elif latitude <= -35 and current_month in {5, 6, 7, 8, 9}:
        summary = "Cooler seasonal pattern with frontal movement and windy changes more likely."
        severity = "info"
        confidence = max(confidence, 0.58)

    if abs(longitude) > 120:
        confidence = min(0.9, confidence + 0.05)

    return summary, severity, confidence


def analyze_location(
    query: str,
    latitude: float,
    longitude: float,
    display_name: str,
    weather_summary: str | None = None,
    weather_severity: str | None = None,
    confidence: float | None = None,
) -> LocationResponse:
    grid = VectorGridIndexer(resolution=settings.default_grid_resolution)
    alert_engine = AlertEngine()
    if weather_summary is None or weather_severity is None or confidence is None:
        weather_summary, weather_severity, confidence = infer_weather_context(latitude, longitude)
    cell = grid.build_cell(
        latitude,
        longitude,
        source="location-search",
        query=query,
        weather_summary=weather_summary,
        severity=weather_severity,
        confidence=confidence,
    )
    route_alert = alert_engine.score_route(query, [cell])
    return LocationResponse(
        query=query,
        display_name=display_name,
        latitude=cell.latitude,
        longitude=cell.longitude,
        h3_index=cell.h3_index,
        weather_summary=weather_summary,
        weather_severity=weather_severity,
      route_alert=asdict(route_alert),
    )


def groq_chat_completion(message: str, location: LocationResponse | None) -> str:
  if location is None:
    location_data = geocode_location(message)
    if location_data is None:
      return "Search a location first so I can answer with the weather API."
    location = analyze_location(
      query=message,
      latitude=float(location_data["latitude"]),
      longitude=float(location_data["longitude"]),
      display_name=str(location_data["display_name"]),
      weather_summary=str(location_data.get("weather_summary", "")),
      weather_severity=str(location_data.get("weather_severity", "info")),
      confidence=float(location_data.get("confidence", 0.6)),
    )

  return (
    f"{location.display_name}: {location.weather_summary} "
    f"Route risk is {location.route_alert.get('severity', 'info')} "
    f"with score {location.route_alert.get('risk_score', 0.0)}."
  )


async def stream_geojson() -> AsyncIterator[str]:
    detector = AIDetector()
    grid = VectorGridIndexer(resolution=settings.default_grid_resolution)
    alert_engine = AlertEngine()

    while True:
        frame = await fetch_latest_radar_frame()
        detections = await detector.detect(frame)
        cell = grid.build_cell(
            settings.default_center_lat,
            settings.default_center_lon,
            detected_at=frame.captured_at.isoformat(),
            source=frame.source,
            severity=detections[0].severity if detections else "info",
            confidence=detections[0].confidence if detections else 0.0,
            detections=[asdict(d) for d in detections],
        )
        route_alert = alert_engine.score_route("default-route", [cell])
        feature_collection = GeoJSONFeatureCollection(
            features=[
                GeoJSONFeature(
                    geometry=GeoJSONGeometry(coordinates=[cell.longitude, cell.latitude]),
                    properties={
                        "h3_index": cell.h3_index,
                        "source": frame.source,
                        "captured_at": frame.captured_at.isoformat(),
                        "detections": [asdict(d) for d in detections],
                        "route_alert": asdict(route_alert),
                    },
                )
            ]
        )
        yield feature_collection.model_dump_json() + "\n"
        await asyncio.sleep(settings.stream_interval_seconds)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "timestamp": datetime.now(tz=timezone.utc).isoformat()}


@app.get(f"{settings.api_prefix}/weather/stream")
async def weather_stream() -> StreamingResponse:
    return StreamingResponse(stream_geojson(), media_type="application/x-ndjson")


@app.post(f"{settings.api_prefix}/routing/score")
async def score_route(request: RouteRequest) -> dict[str, object]:
    grid = VectorGridIndexer(resolution=settings.default_grid_resolution)
    alert_engine = AlertEngine()
    cells = [grid.build_cell(latitude, longitude) for latitude, longitude in request.coordinates]
    alert = alert_engine.score_route(request.route_id, cells)
    return {"route_id": request.route_id, "alert": asdict(alert)}


@app.get(f"{settings.api_prefix}/weather/search")
async def search_weather_locations(query: str) -> list[SearchCandidate]:
    candidates = call_photon_api(query)
    return [SearchCandidate(**c) for c in candidates]


@app.post(f"{settings.api_prefix}/weather/location")
async def weather_location(request: LocationQuery) -> LocationResponse:
    geocoded = geocode_location(request.query)
    if geocoded is None:
        return LocationResponse(
            query=request.query,
            display_name=request.query,
            latitude=settings.default_center_lat,
            longitude=settings.default_center_lon,
            h3_index="unresolved",
            weather_summary="Location not found. Try a broader city, region, or country name.",
            weather_severity="info",
            route_alert={"severity": "info", "message": "No geocode match found", "risk_score": 0.0, "cell_count": 0},
        )

    return analyze_location(
        query=request.query,
        latitude=geocoded["latitude"],
        longitude=geocoded["longitude"],
        display_name=geocoded["display_name"],
        weather_summary=geocoded.get("weather_summary"),
        weather_severity=geocoded.get("weather_severity"),
        confidence=geocoded.get("confidence"),
    )


@app.post(f"{settings.api_prefix}/chat/respond")
async def chat_respond(request: ChatRequest) -> dict[str, object]:
    location_response: LocationResponse | None = None

    if request.latitude is not None and request.longitude is not None:
        geocoded = geocode_location(f"{request.latitude}, {request.longitude}")
        if geocoded is not None:
            location_response = analyze_location(
                query=request.location or request.message,
                latitude=geocoded["latitude"],
                longitude=geocoded["longitude"],
                display_name=geocoded["display_name"],
                weather_summary=geocoded.get("weather_summary"),
                weather_severity=geocoded.get("weather_severity"),
                confidence=geocoded.get("confidence"),
            )
    elif request.location:
        geocoded = geocode_location(request.location)
        if geocoded is not None:
            location_response = analyze_location(
                query=request.location,
                latitude=geocoded["latitude"],
                longitude=geocoded["longitude"],
                display_name=geocoded["display_name"],
                weather_summary=geocoded.get("weather_summary"),
                weather_severity=geocoded.get("weather_severity"),
                confidence=geocoded.get("confidence"),
            )

    reply = groq_chat_completion(request.message, location_response)
    return {"reply": reply, "location": location_response.model_dump() if location_response else None}


@app.get("/")
async def root() -> HTMLResponse:
    html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AI Weather Map</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
    <style>
      :root {
        color-scheme: dark;
        --panel: rgba(10, 20, 35, 0.9);
        --text: #edf4ff;
        --muted: #98aac3;
        --accent: #63d7ff;
        --accent-2: #72f0a8;
        --warning: #ffcb57;
        --critical: #ff6d7a;
        --border: rgba(255, 255, 255, 0.12);
      }
      * { box-sizing: border-box; }
      html, body { height: 100%; }
      body {
        margin: 0;
        font-family: Inter, Segoe UI, Arial, sans-serif;
        color: var(--text);
        background:
          radial-gradient(circle at top left, rgba(99, 215, 255, 0.18), transparent 24%),
          radial-gradient(circle at bottom right, rgba(114, 240, 168, 0.14), transparent 30%),
          linear-gradient(135deg, #06101d 0%, #0b1728 50%, #07111f 100%);
      }
      .app { min-height: 100vh; display: grid; grid-template-columns: 360px minmax(0, 1fr); }
      .sidebar { padding: 24px 20px; border-right: 1px solid var(--border); background: linear-gradient(180deg, rgba(6, 16, 29, 0.94), rgba(8, 15, 28, 0.96)); backdrop-filter: blur(18px); overflow: auto; }
      .eyebrow { display: inline-flex; padding: 6px 12px; border-radius: 999px; background: rgba(99, 215, 255, 0.12); color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; }
      h1 { margin: 16px 0 10px; font-size: clamp(2rem, 5vw, 3.2rem); line-height: 0.96; }
      .lede { margin: 0 0 18px; color: var(--muted); line-height: 1.65; font-size: 0.98rem; }
      .panel, .chat-panel { padding: 16px; border: 1px solid var(--border); border-radius: 18px; background: rgba(255, 255, 255, 0.04); }
      .stack { display: grid; gap: 12px; }
      .field { display: grid; gap: 8px; }
      .field label { font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }
      .field input, .field textarea { width: 100%; padding: 12px 14px; border-radius: 14px; border: 1px solid rgba(255,255,255,0.1); background: rgba(3, 9, 19, 0.8); color: var(--text); outline: none; font: inherit; }
      .field textarea { min-height: 92px; resize: vertical; }
      .row { display: flex; gap: 10px; flex-wrap: wrap; }
      button { display: inline-flex; align-items: center; justify-content: center; min-width: 140px; padding: 12px 16px; border-radius: 14px; border: 1px solid var(--border); font-weight: 800; cursor: pointer; }
      .primary { background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #04111f; }
      .secondary { color: var(--text); background: rgba(255,255,255,0.04); }
      .pill { display: inline-flex; padding: 5px 10px; border-radius: 999px; font-size: 12px; font-weight: 800; background: rgba(99, 215, 255, 0.12); color: var(--accent); }
      .pill.warning { background: rgba(255, 203, 87, 0.12); color: var(--warning); }
      .pill.critical { background: rgba(255, 109, 122, 0.12); color: var(--critical); }
      .stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 14px 0; }
      .stat { padding: 14px; border-radius: 16px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08); }
      .stat-label { display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }
      .stat-value { display: block; margin-top: 6px; font-size: 1.05rem; font-weight: 800; }
      .weather-list, .chat-log { display: grid; gap: 12px; margin-top: 14px; }
      .weather-card, .chat-bubble { padding: 14px; border-radius: 16px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); }
      .chat-bubble.user { background: rgba(99, 215, 255, 0.08); }
      .chat-bubble.assistant { background: rgba(114, 240, 168, 0.08); }
      .map-wrap { position: relative; height: 100vh; }
      #map { width: 100%; height: 100%; }
      .map-overlay { position: absolute; top: 18px; left: 18px; z-index: 500; padding: 14px 16px; border-radius: 16px; max-width: 390px; background: rgba(7, 14, 26, 0.78); border: 1px solid rgba(255,255,255,0.12); backdrop-filter: blur(12px); }
      .map-overlay small { display: block; color: var(--muted); margin-top: 6px; line-height: 1.4; }
      .map-note { position: absolute; bottom: 18px; left: 18px; z-index: 500; padding: 12px 14px; border-radius: 14px; background: rgba(7, 14, 26, 0.78); border: 1px solid rgba(255,255,255,0.12); backdrop-filter: blur(12px); max-width: 390px; }
      .map-note.warn { color: #ffe38a; }
      .weather-overlay {
        position: absolute;
        top: 18px;
        right: 18px;
        z-index: 500;
        padding: 14px 16px;
        border-radius: 16px;
        max-width: 360px;
        background: rgba(7, 14, 26, 0.78);
        border: 1px solid rgba(255,255,255,0.12);
        backdrop-filter: blur(12px);
        display: none;
      }
      .weather-overlay h2 { margin: 0 0 8px; font-size: 1rem; }
      .weather-overlay p { margin: 8px 0 0; color: var(--muted); line-height: 1.45; }
      .weather-overlay .meta { margin-top: 10px; display: grid; gap: 6px; font-size: 0.95rem; }
      
      /* Autocomplete suggestions wrapper and list */
      .autocomplete-wrapper {
        position: relative;
        width: 100%;
      }
      .suggestions-list {
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        z-index: 1000;
        max-height: 250px;
        overflow-y: auto;
        margin: 4px 0 0;
        padding: 6px;
        list-style: none;
        background: rgba(10, 20, 35, 0.94);
        border: 1px solid var(--border);
        border-radius: 14px;
        backdrop-filter: blur(16px);
        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        display: none;
      }
      .suggestions-list li {
        padding: 10px 14px;
        border-radius: 10px;
        cursor: pointer;
        font-size: 0.92rem;
        transition: all 0.2s ease;
        display: flex;
        align-items: center;
        gap: 8px;
        color: var(--text);
      }
      .suggestions-list li:hover, .suggestions-list li.active {
        background: rgba(99, 215, 255, 0.15);
        color: var(--accent);
      }
      .suggestions-list li .meta {
        font-size: 0.78rem;
        color: var(--muted);
        margin-left: auto;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        background: rgba(255,255,255,0.06);
        padding: 2px 6px;
        border-radius: 4px;
      }
      @media (max-width: 980px) { .app { grid-template-columns: 1fr; } .sidebar { border-right: 0; border-bottom: 1px solid var(--border); } .map-wrap { height: 50vh; min-height: 400px; } }
    </style>
  </head>
  <body>
    <div class="app">
      <aside class="sidebar">
        <div class="eyebrow">AI Weather Map</div>
        <h1>Search a location, see the map, ask the chatbot.</h1>
        <p class="lede">This version keeps the page simple: one search box, one weather feed, one local summary panel. No extra API services are required.</p>

        <div class="panel stack">
          <div class="field">
            <label for="locationInput">Location</label>
            <div class="autocomplete-wrapper">
              <input id="locationInput" type="text" placeholder="e.g. Dallas, Texas" autocomplete="off" />
              <ul id="suggestions" class="suggestions-list"></ul>
            </div>
          </div>
          <div class="row">
            <button id="findLocationBtn" class="primary" type="button">Find Location</button>
          </div>
          <div id="streamStatus" class="pill">Ready</div>

          <div class="stats">
            <div class="stat"><span class="stat-label">Selected location</span><span id="selectedLocation" class="stat-value">None</span></div>
            <div class="stat"><span class="stat-label">Route risk</span><span id="routeRisk" class="stat-value">--</span></div>
          </div>

          <div class="weather-list" id="weatherList"></div>
        </div>

        <div class="chat-panel" style="margin-top: 14px;">
          <div class="eyebrow" style="margin-bottom: 12px;">Weather Summary</div>
          <div class="field">
            <label for="chatInput">Ask about weather, route risk, or a place</label>
            <textarea id="chatInput" placeholder="Will it be safe in Miami today?"></textarea>
          </div>
          <div class="row">
            <button id="sendChatBtn" class="primary" type="button">Send</button>
            <button id="clearChatBtn" class="secondary" type="button">Clear</button>
          </div>
          <div id="chatLog" class="chat-log"></div>
        </div>
      </aside>

      <main class="map-wrap">
        <div class="map-overlay">
          <strong>Weather View</strong>
          <small>Search a place to load weather data. The view falls back to a local summary when the weather API key is not configured.</small>
        </div>
        <div id="map"></div>
        <div id="weatherOverlay" class="weather-overlay"></div>
        <div id="mapNote" class="map-note" style="display: none;"></div>
      </main>
    </div>

    <script>
      let map = null;
      let mapMarker = null;
      let currentLocation = null;

      const streamStatus = document.getElementById('streamStatus');
      const weatherList = document.getElementById('weatherList');
      const routeRisk = document.getElementById('routeRisk');
      const selectedLocation = document.getElementById('selectedLocation');
      const locationInput = document.getElementById('locationInput');
      const findLocationBtn = document.getElementById('findLocationBtn');
      const chatInput = document.getElementById('chatInput');
      const sendChatBtn = document.getElementById('sendChatBtn');
      const clearChatBtn = document.getElementById('clearChatBtn');
      const chatLog = document.getElementById('chatLog');
      const mapNote = document.getElementById('mapNote');
      const weatherOverlay = document.getElementById('weatherOverlay');

      function severityClass(value) { return value === 'critical' ? 'critical' : value === 'warning' ? 'warning' : ''; }

      function addMessage(role, text) {
        const bubble = document.createElement('div');
        bubble.className = `chat-bubble ${role}`;
        bubble.textContent = text;
        chatLog.prepend(bubble);
      }

      function showMapNote(text, isWarning = false) {
        mapNote.style.display = 'block';
        mapNote.className = isWarning ? 'map-note warn' : 'map-note';
        mapNote.textContent = text;
      }

      function showWeatherOverlay(location) {
        weatherOverlay.style.display = 'block';
        weatherOverlay.innerHTML = `
          <h2>${location.display_name}</h2>
          <span class="pill ${severityClass(location.weather_severity)}">${location.weather_severity}</span>
          <p>${location.weather_summary}</p>
          <div class="meta">
            <div><strong>Route risk:</strong> ${location.route_alert?.severity || 'info'} (${location.route_alert?.risk_score ?? '--'})</div>
            <div><strong>Coordinates:</strong> ${location.latitude.toFixed(4)}, ${location.longitude.toFixed(4)}</div>
          </div>
        `;
      }

      let isLeaflet = false;

      function showLocation(location) {
        currentLocation = location;
        selectedLocation.textContent = location.display_name;
        routeRisk.textContent = location.route_alert?.risk_score ?? '--';
        weatherList.innerHTML = `
          <div class="weather-card">
            <strong>Weather Snapshot</strong>
            <div><span class="pill ${severityClass(location.weather_severity)}">${location.weather_severity}</span></div>
            <p style="color: var(--muted); margin: 10px 0 0;">${location.weather_summary}</p>
            <p style="color: var(--muted); margin: 10px 0 0;"><strong>Route:</strong> ${location.route_alert?.message || 'No route warning available.'}</p>
          </div>
        `;
        showWeatherOverlay(location);

        const position = { lat: location.latitude, lng: location.longitude };

        if (isLeaflet && map) {
          const latlng = [location.latitude, location.longitude];
          map.setView(latlng, 8);
          if (mapMarker) {
            mapMarker.setLatLng(latlng);
          } else {
            mapMarker = L.marker(latlng).addTo(map);
          }
          showMapNote(`Loaded weather data for ${location.display_name}.`, false);
        } else if (window.google && google.maps) {
          if (!map) {
            map = new google.maps.Map(document.getElementById('map'), {
              center: position,
              zoom: 8,
              mapTypeControl: false,
              streetViewControl: false,
              fullscreenControl: true,
            });
          } else {
            map.setCenter(position);
            map.setZoom(8);
          }
          if (mapMarker) {
            mapMarker.setPosition(position);
          } else {
            mapMarker = new google.maps.Marker({ position, map, title: location.display_name });
          }
          showMapNote(`Loaded weather data for ${location.display_name}.`, false);
        }
      }

      async function lookupLocation() {
        const query = locationInput.value.trim();
        if (!query) {
          addMessage('assistant', 'Type a city, region, or country name first.');
          return;
        }
        addMessage('user', `Search: ${query}`);
        streamStatus.textContent = 'Searching location...';
        try {
          const response = await fetch('/v1/weather/location', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query }),
          });
          const data = await response.json();
          showLocation(data);
          streamStatus.textContent = 'Location loaded';
          addMessage('assistant', `${data.display_name}: ${data.weather_summary}`);
        } catch (error) {
          streamStatus.textContent = 'Location lookup failed';
          addMessage('assistant', `I could not look up that location: ${error.message}`);
        }
      }

      async function sendChat() {
        const message = chatInput.value.trim();
        if (!message) return;
        addMessage('user', message);
        chatInput.value = '';
        try {
          const response = await fetch('/v1/chat/respond', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message,
              location: currentLocation ? currentLocation.display_name : null,
              latitude: currentLocation ? currentLocation.latitude : null,
              longitude: currentLocation ? currentLocation.longitude : null,
            }),
          });
          const data = await response.json();
          addMessage('assistant', data.reply);
          if (data.location) showLocation(data.location);
        } catch (error) {
          addMessage('assistant', `Chat request failed: ${error.message}`);
        }
      }

      function initLeafletMap() {
        isLeaflet = true;
        const defaultCenter = [DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON];
        map = L.map('map').setView(defaultCenter, 4);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
          subdomains: 'abcd',
          maxZoom: 20
        }).addTo(map);
        showMapNote('Leaflet Map (OpenStreetMap) loaded successfully.', false);
        streamStatus.textContent = 'Ready';
      }

      function initializeMapFallback() {
        initLeafletMap();
      }

      window.initMap = function initMap() {
        const defaultCenter = { lat: DEFAULT_CENTER_LAT, lng: DEFAULT_CENTER_LON };
        if (window.google && google.maps) {
          map = new google.maps.Map(document.getElementById('map'), {
            center: defaultCenter,
            zoom: 4,
            mapTypeControl: false,
            streetViewControl: false,
            fullscreenControl: true,
          });
          showMapNote('Google Maps loaded successfully. Search a location to center.', false);
          streamStatus.textContent = 'Ready';
        } else {
          initLeafletMap();
        }
      };

      const suggestions = document.getElementById('suggestions');
      let debounceTimeout = null;
      let activeSuggestionIndex = -1;
      let suggestionsData = [];

      function showSuggestions() {
        if (suggestionsData.length > 0) {
          suggestions.style.display = 'block';
        }
      }

      function hideSuggestions() {
        suggestions.style.display = 'none';
        activeSuggestionIndex = -1;
      }

      function selectSuggestion(index) {
        const item = suggestionsData[index];
        if (!item) return;
        
        locationInput.value = item.display_name;
        hideSuggestions();
        lookupLocation();
      }

      function fetchSuggestions(query) {
        if (!query) {
          suggestionsData = [];
          hideSuggestions();
          return;
        }
        
        fetch(`/v1/weather/search?query=${encodeURIComponent(query)}`)
          .then(res => res.json())
          .then(data => {
            suggestionsData = data;
            renderSuggestions();
          })
          .catch(err => {
            console.error("Suggestions fetch error:", err);
          });
      }

      function renderSuggestions() {
        suggestions.innerHTML = '';
        if (suggestionsData.length === 0) {
          hideSuggestions();
          return;
        }
        
        suggestionsData.forEach((item, index) => {
          const li = document.createElement('li');
          
          const icon = document.createElement('span');
          icon.className = 'location-icon';
          icon.innerHTML = '📍';
          li.appendChild(icon);
          
          const textSpan = document.createElement('span');
          textSpan.textContent = item.display_name;
          li.appendChild(textSpan);
          
          if (item.osm_value) {
            const meta = document.createElement('span');
            meta.className = 'meta';
            meta.textContent = item.osm_value;
            li.appendChild(meta);
          }
          
          li.addEventListener('mousedown', (e) => {
            e.preventDefault();
            selectSuggestion(index);
          });
          
          suggestions.appendChild(li);
        });
        
        showSuggestions();
      }

      locationInput.addEventListener('input', () => {
        const query = locationInput.value.trim();
        clearTimeout(debounceTimeout);
        
        if (query.length < 2) {
          suggestionsData = [];
          hideSuggestions();
          return;
        }
        
        debounceTimeout = setTimeout(() => {
          fetchSuggestions(query);
        }, 300);
      });

      locationInput.addEventListener('focus', () => {
        showSuggestions();
      });

      locationInput.addEventListener('blur', () => {
        setTimeout(hideSuggestions, 200);
      });

      locationInput.addEventListener('keydown', (e) => {
        if (suggestions.style.display === 'block' && suggestionsData.length > 0) {
          const items = suggestions.querySelectorAll('li');
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            activeSuggestionIndex = (activeSuggestionIndex + 1) % suggestionsData.length;
            updateActiveSuggestion(items);
            return;
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            activeSuggestionIndex = (activeSuggestionIndex - 1 + suggestionsData.length) % suggestionsData.length;
            updateActiveSuggestion(items);
            return;
          } else if (e.key === 'Enter') {
            if (activeSuggestionIndex >= 0) {
              e.preventDefault();
              selectSuggestion(activeSuggestionIndex);
              return;
            }
          } else if (e.key === 'Escape') {
            hideSuggestions();
            return;
          }
        }
        if (e.key === 'Enter') {
          lookupLocation();
        }
      });

      function updateActiveSuggestion(items) {
        items.forEach((item, index) => {
          if (index === activeSuggestionIndex) {
            item.classList.add('active');
            item.scrollIntoView({ block: 'nearest' });
          } else {
            item.classList.remove('active');
          }
        });
      }

      window.addEventListener('resize', () => {
        if (isLeaflet && map) {
          map.invalidateSize();
        }
      });

      findLocationBtn.addEventListener('click', lookupLocation);
      sendChatBtn.addEventListener('click', sendChat);
      chatInput.addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') sendChat(); });
      clearChatBtn.addEventListener('click', () => { chatLog.innerHTML = ''; });

      addMessage('assistant', 'Type a location to start. I will place it on the map and summarize the weather there.');

      if (!window.APP_SETTINGS.googleMapsApiKey) {
        initLeafletMap();
      } else {
        streamStatus.textContent = 'Loading Google Maps...';
      }
    </script>
  </body>
</html>
    """
    # Choose map resources (Google Maps or Leaflet)
    if settings.google_maps_api_key:
        map_resources = f'<script src="https://maps.googleapis.com/maps/api/js?key={settings.google_maps_api_key}&callback=initMap" async defer></script>'
    else:
        map_resources = """
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
        """

    # Inject APP_SETTINGS and map resources into <head>
    settings_script = f"""
    <script>
      window.APP_SETTINGS = {{
        googleMapsApiKey: "{settings.google_maps_api_key}",
        defaultCenterLat: {settings.default_center_lat},
        defaultCenterLon: {settings.default_center_lon}
      }};
    </script>
    """
    
    modified_html = html.replace("<head>", f"<head>\n{settings_script}\n{map_resources}")
    modified_html = modified_html.replace("DEFAULT_CENTER_LAT", str(settings.default_center_lat))
    modified_html = modified_html.replace("DEFAULT_CENTER_LON", str(settings.default_center_lon))
    return HTMLResponse(content=modified_html)
