import httpx
import pytest
from fastapi.testclient import TestClient

from citypulse import api, sources, travel
from citypulse.sources import SOURCES, CityNotFound, Source
from tests.conftest import NAIROBI

PLACES = {
    "nairobi": NAIROBI,
    "oslo": {**NAIROBI, "id": 3143244, "name": "Oslo", "country": "Norway", "country_code": "NO",
             "latitude": 59.91, "longitude": 10.75},
}

FAKE_DATA = {
    "weather": {"temperature_2m": 17.3, "wind_speed_10m": 9, "weather_code": 3, "weather": "Overcast"},
    "forecast": {"days": [], "sunrise": "06:13", "sunset": "18:24", "timezone": "Africa/Nairobi",
                 "utc_offset_seconds": 10800},
    "air": {"us_aqi": 28, "pm2_5": 5.0, "aqi_category": "Good"},
    "quakes": {"n": 0, "max": None},
    "disasters": {"n": 0, "items": []},
    "crime": {"homicide_100k": {"v": 4.9, "yr": "2022"}},
    "country": {"cca3": "KEN", "name": "Kenya", "capital": "Nairobi", "region": "Sub-Saharan Africa",
                "income": "Lower middle income", "currencies": {"KES": {}}},
    "fx": {"cur": "KES", "per_usd": 129.98, "date": "2026-11-20"},
    "vaccines": {"dtp3": {"v": 88, "yr": 2025}, "mcv1": {"v": 80, "yr": 2025}},
}


@pytest.fixture
def client(monkeypatch):
    async def fake_find_place(name, cc=None):
        place = PLACES.get(name.lower())
        if not place or (cc and cc != place["country_code"]):
            raise CityNotFound(name, cc)
        return place

    monkeypatch.setattr(api, "find_place", fake_find_place)
    for name, data in FAKE_DATA.items():
        async def fetch(place, data=data):
            return data
        old = SOURCES[name]
        monkeypatch.setitem(SOURCES, name, Source(old.ttl, fetch, old.scope))
    with TestClient(api.app) as c:
        yield c


def test_dashboard_served_for_get_and_head(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>CityPulse</title>" in r.text
    assert client.head("/").status_code == 200


def test_city_snapshot(client):
    j = client.get("/city/Nairobi").json()
    assert j["city"]["name"] == "Nairobi" and "local_time" in j["city"]
    assert set(j["res"]) == set(SOURCES)
    assert all(v["cache"] == "miss" for v in j["res"].values())
    assert j["pulse"]["based_on"] == "6 of 6 readings"
    again = client.get("/city/Nairobi?src=air").json()
    assert again["res"]["air"]["cache"] == "hit" and list(again["res"]) == ["air"]


def test_city_errors(client):
    assert client.get("/city/Nowhere").status_code == 404
    assert client.get("/city/Nairobi?country=NO").json() == {"detail": "city not found in country NO"}
    assert client.get("/city/Nairobi?country=KEN").status_code == 422
    assert client.get("/city/Nairobi?src=nope").status_code == 400


def test_failing_source_is_isolated_and_hides_details(client, monkeypatch):
    async def broken(place):
        request = httpx.Request("GET", "https://secret-upstream.example/api?key=abc")
        raise httpx.ConnectTimeout("timed out", request=request)

    monkeypatch.setitem(SOURCES, "air", Source(60, broken, "city"))
    j = client.get("/city/Nairobi").json()
    assert j["res"]["air"] == {"data": None, "err": "source timed out"}
    assert j["res"]["weather"]["data"] is not None
    assert "secret-upstream" not in str(j)


def test_city_lookup_failure_is_a_clean_502(client, monkeypatch):
    async def down(name, cc=None):
        raise httpx.ConnectError("dns", request=httpx.Request("GET", "https://geo.example/?q=x"))

    monkeypatch.setattr(api, "find_place", down)
    r = client.get("/city/Lima")
    assert r.status_code == 502
    assert r.json() == {"detail": "city lookup failed: source unreachable"}


def test_compare(client):
    j = client.get("/compare?city=Nairobi&city=Oslo&city=Nowhere").json()
    assert [c.get("err") for c in j["cities"]] == [None, None, "city not found"]
    assert len(j["ranking"]) == 2
    assert client.get("/compare?city=Nairobi").status_code == 400
    assert client.get("/compare?city=Nairobi&city=Oslo,NOR").status_code == 400


def fake_sections(place, names=None):
    async def load():
        months = [{"month": m, "avg_high": 26, "avg_low": 15, "rain_mm": 20, "rainy_days": 2,
                   "sunshine_hours": 9, "humidity": 60, "daylight_hours": 12} for m in range(1, 13)]
        return {
            "climate": {"months": months, "years": "2023-2025", "source": "test"},
            "safety": {"homicide_100k": {"v": 1, "yr": "2022", "source": "test"}},
            "advisory": {"level": 0, "label": "No travel warnings", "source": "test"},
            "health": {"basic_water_pct": {"v": 100, "yr": 2024, "source": "test"}},
            "outbreaks": {"country": [], "worldwide": [], "source": "test"},
            "hazards": {"events": [], "red": 0, "orange": 0, "source": "test"},
            "costs": {"price_level": {"v": 0.4, "yr": "2025", "label": "Cheap", "source": "test"}},
        }
    return load()


@pytest.fixture
def trip_client(client, monkeypatch):
    monkeypatch.setattr(travel, "load_sections", fake_sections)

    async def no_holidays(cc, year):
        return [{"date": f"{year}-07-04", "name": "Test Day", "local_name": "Test Day"}]

    monkeypatch.setattr(travel, "fetch_holidays", no_holidays)
    return client


def test_trip_report(trip_client):
    j = trip_client.get("/trip/Nairobi?month=7").json()
    assert j["month_name"] == "July" and j["city"]["country_code"] == "KE"
    assert j["trip"]["grade"] == "Great time to go"
    assert j["climate"]["avg_high"] == 26 and len(j["climate_by_month"]) == 12
    assert j["practical"]["holidays_in_month"][0]["name"] == "Test Day"
    assert j["practical"]["languages"] == ["Swahili", "English"]
    assert any("Test Day" in c for c in j["cautions"])
    assert j["unavailable"] == []


def test_trip_validation(trip_client):
    assert trip_client.get("/trip/Nairobi?month=13").status_code == 422
    assert trip_client.get("/trip/Nowhere?month=7").status_code == 404
    assert 1 <= trip_client.get("/trip/Nairobi").json()["month"] <= 12  # defaults to next month


def test_destinations_rank_shortlist(trip_client):
    j = trip_client.get("/destinations?month=7&city=Nairobi&city=Oslo&city=Nowhere").json()
    assert [d["name"] for d in j["destinations"]] == ["Nairobi", "Oslo"] or len(j["destinations"]) == 2
    assert all(d["complete"] for d in j["destinations"])
    assert j["not_ranked"] == [{"query": "Nowhere", "err": "city not found"}]
    assert trip_client.get("/destinations?city=Nairobi").status_code == 400


def test_incomplete_destinations_rank_last(trip_client, monkeypatch):
    async def sections(place, names=None):
        s = await fake_sections(place)
        if place["name"] == "Oslo":
            s["climate"] = None  # weather unavailable: a safety-only score must not win
            s["safety"]["homicide_100k"]["v"] = 0
        return s

    monkeypatch.setattr(travel, "load_sections", sections)
    j = trip_client.get("/destinations?month=7&city=Oslo&city=Nairobi").json()
    assert [d["name"] for d in j["destinations"]] == ["Nairobi", "Oslo"]
    assert j["destinations"][1]["complete"] is False
