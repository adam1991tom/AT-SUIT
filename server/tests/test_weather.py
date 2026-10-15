"""The venue weather: off until an admin picks a town; only its position is sent."""
from atsuit.modules import weather


def test_weather(admin, monkeypatch):
    sent = []

    def fake(url, params):
        sent.append((url, params))
        if url == weather.GEOCODE:
            return {"results": [{"name": "Harrogate", "admin1": "England", "country": "United Kingdom", "latitude": 53.99078, "longitude": -1.5373}]}
        return {"current": {"temperature_2m": 13.6, "apparent_temperature": 11.2, "weather_code": 61, "wind_speed_10m": 9.4, "precipitation": 0.2, "time": "2026-10-15T14:00"},
                "daily": {"temperature_2m_max": [15.1], "temperature_2m_min": [8.0], "precipitation_probability_max": [70],
                          "sunrise": ["2026-10-15T07:31"], "sunset": ["2026-10-15T18:14"]}}

    monkeypatch.setattr(weather, "_get", fake)
    weather._cache.clear()
    assert admin.get("/api/weather").json() == {"place": None}
    assert sent == []  # nothing leaves the server until a town is picked

    found = admin.get("/api/admin/weather/find", params={"q": "Harrogate"}).json()
    assert found == [{"name": "Harrogate, England, United Kingdom", "lat": 53.991, "lon": -1.537}]
    site = admin.get("/api/admin/weather").json()[0]["site_id"]
    assert admin.put(f"/api/admin/weather/{site}", json=found[0]).status_code == 200
    w = admin.get("/api/weather").json()
    assert (w["place"], w["temp"], w["text"], w["high"], w["rain_chance"], w["sunset"]) == ("Harrogate, England, United Kingdom", 13.6, "Light rain", 15.1, 70, "18:14")
    params = sent[-1][1]
    assert (params["latitude"], params["longitude"]) == (53.991, -1.537) and "Harrogate" not in str(params)
    admin.get("/api/weather")
    assert len(sent) == 2  # kept for a quarter of an hour

    # no internet: the last forecast stays, marked stale
    def down(url, params):
        raise weather.httpx.ConnectError("no route")
    monkeypatch.setattr(weather, "_get", down)
    weather._cache[site] = (0, weather._cache[site][1])
    assert admin.get("/api/weather").json()["stale"] is True

    assert admin.delete(f"/api/admin/weather/{site}").status_code == 200
    assert admin.get("/api/weather").json() == {"place": None}
