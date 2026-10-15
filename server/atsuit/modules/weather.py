"""The weather at the venue, for the top bar of the tech workspace and the
console dashboard. An admin picks the venue's town once per site; until then
nothing is fetched. Forecasts come from Open-Meteo (no account or key), and only
the site's latitude and longitude are sent: no names, rooms or anything else.
"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..security import Principal, require_admin, require_user

router = APIRouter()

FORECAST = "https://api.open-meteo.com/v1/forecast"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FRESH = 15 * 60  # seconds a forecast is kept
_cache: dict[int, tuple[float, dict]] = {}

# WMO weather codes, as Open-Meteo sends them
CODES = {
    0: ("Clear", "☀️"), 1: ("Mostly clear", "🌤️"), 2: ("Partly cloudy", "⛅"), 3: ("Cloudy", "☁️"),
    45: ("Fog", "🌫️"), 48: ("Freezing fog", "🌫️"),
    51: ("Light drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Heavy drizzle", "🌧️"), 56: ("Freezing drizzle", "🌧️"), 57: ("Freezing drizzle", "🌧️"),
    61: ("Light rain", "🌦️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"), 66: ("Freezing rain", "🌧️"), 67: ("Freezing rain", "🌧️"),
    71: ("Light snow", "🌨️"), 73: ("Snow", "🌨️"), 75: ("Heavy snow", "❄️"), 77: ("Snow grains", "🌨️"),
    80: ("Showers", "🌦️"), 81: ("Showers", "🌧️"), 82: ("Heavy showers", "⛈️"), 85: ("Snow showers", "🌨️"), 86: ("Snow showers", "❄️"),
    95: ("Thunderstorm", "⛈️"), 96: ("Thunderstorm, hail", "⛈️"), 99: ("Thunderstorm, hail", "⛈️"),
}


def _get(url: str, params: dict) -> dict:
    r = httpx.get(url, params=params, timeout=6)
    r.raise_for_status()
    return r.json()


def place(c, site_id: int) -> dict | None:
    return db.get_setting(c, f"weather:{site_id}")


def _site_for(c, p: Principal, site_id: int | None) -> int | None:
    if p.site_id is not None:
        return p.site_id
    if site_id is not None:
        return site_id
    r = c.execute("SELECT id FROM sites ORDER BY id LIMIT 1").fetchone()
    return r["id"] if r else None


def forecast(lat: float, lon: float) -> dict:
    d = _get(FORECAST, {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1, "wind_speed_unit": "mph",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
    })
    cur, day = d.get("current", {}), d.get("daily", {})
    first = lambda k: (day.get(k) or [None])[0]  # noqa: E731
    text, icon = CODES.get(cur.get("weather_code"), ("", "🌡️"))
    return {
        "temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"), "text": text, "icon": icon,
        "wind_mph": cur.get("wind_speed_10m"), "rain_mm": cur.get("precipitation"),
        "high": first("temperature_2m_max"), "low": first("temperature_2m_min"), "rain_chance": first("precipitation_probability_max"),
        "sunrise": (first("sunrise") or "")[11:16], "sunset": (first("sunset") or "")[11:16], "at": cur.get("time"),
    }


@router.get("/api/weather")
def weather(site_id: int | None = None, p: Principal = Depends(require_user)):
    """The weather at the venue now and today. Empty until an admin sets the town."""
    with db.ro() as c:
        sid = _site_for(c, p, site_id)
        where = place(c, sid) if sid else None
    if not where:
        return {"place": None}
    now = time.time()
    hit = _cache.get(sid)
    if hit and hit[1].get("_key") == (where["lat"], where["lon"]) and now - hit[0] < FRESH:
        return {k: v for k, v in hit[1].items() if k != "_key"}
    try:
        out = {"place": where["name"], "ok": True, **forecast(where["lat"], where["lon"])}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        # No internet at the venue, or Open-Meteo is down: keep the last forecast if there is one.
        if hit:
            return {**{k: v for k, v in hit[1].items() if k != "_key"}, "stale": True}
        return {"place": where["name"], "ok": False, "reason": f"Can't get the weather: {type(e).__name__}"}
    _cache[sid] = (now, {**out, "_key": (where["lat"], where["lon"])})
    return out


@router.get("/api/admin/weather/find")
def find_place(q: str, p: Principal = Depends(require_admin)):
    """Towns matching what the admin typed, to pick the venue's."""
    q = q.strip()
    if len(q) < 2:
        return []
    try:
        d = _get(GEOCODE, {"name": q[:80], "count": 6, "language": "en", "format": "json"})
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"Can't look up places from this server ({type(e).__name__}). Does it have internet access?")
    return [{"name": ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x),
             "lat": round(r["latitude"], 3), "lon": round(r["longitude"], 3)} for r in d.get("results") or []]


class PlaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


@router.put("/api/admin/weather/{site_id}")
def set_place(site_id: int, body: PlaceIn, p: Principal = Depends(require_admin)):
    """The venue's town for the weather."""
    return _set(site_id, body, p)


@router.delete("/api/admin/weather/{site_id}")
def clear_place(site_id: int, p: Principal = Depends(require_admin)):
    """No weather for this site: nothing is fetched."""
    return _set(site_id, None, p)


def _set(site_id: int, body: PlaceIn | None, p: Principal) -> dict:
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM sites WHERE id=?", (site_id,)).fetchone():
            raise HTTPException(404, "Site not found")
        db.set_setting(c, f"weather:{site_id}", body.model_dump() if body else None)
        db.audit(c, p.name, "weather.place", body.name if body else "off")
    _cache.pop(site_id, None)
    return {"ok": True}


@router.get("/api/admin/weather")
def places(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        return [{"site_id": s["id"], "site": s["name"], "place": place(c, s["id"])} for s in c.execute("SELECT id,name FROM sites ORDER BY name")]
