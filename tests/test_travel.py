import asyncio
from datetime import datetime, timedelta, timezone

from citypulse import travel
from tests.conftest import NAIROBI

TODAY = datetime.now(timezone.utc).date()


def run(coro):
    return asyncio.run(coro)


def test_climate_normals_from_daily_history(monkeypatch):
    days = [f"{y}-{m:02d}-{d:02d}" for y in (2023, 2024, 2025) for m in (1, 7) for d in range(1, 11)]

    async def fake_archive(place, start, end):
        return {"daily": {
            "time": days,
            "temperature_2m_max": [30.0 if d[5:7] == "01" else 20.0 for d in days],
            "temperature_2m_min": [20.0 if d[5:7] == "01" else 10.0 for d in days],
            "precipitation_sum": [5.0 if d[5:7] == "01" else 0.0 for d in days],
            "sunshine_duration": [36000.0] * len(days),
            "relative_humidity_2m_mean": [70.0] * len(days),
        }}

    monkeypatch.setattr(travel, "_archive", fake_archive)
    normals = run(travel.fetch_climate(NAIROBI, use_bundled=False))
    jan, jul = normals["months"][0], normals["months"][6]
    assert (jan["avg_high"], jan["avg_low"], jul["avg_high"]) == (30.0, 20.0, 20.0)
    assert jan["rainy_days"] == 10.0 and jan["rain_mm"] == 50   # 10 wet days a year, 5 mm each
    assert jul["rainy_days"] == 0.0
    assert jan["sunshine_hours"] == 10.0
    assert normals["months"][3]["avg_high"] is None             # no April data in the fake history


def test_bundled_climate_is_used_when_available(monkeypatch):
    bundled = {"months": [], "years": "x", "source": "bundled"}
    monkeypatch.setitem(travel.BUNDLED_CLIMATE, travel.climate_key(NAIROBI), bundled)

    async def must_not_call(*a):
        raise AssertionError("archive should not be called for bundled cities")

    monkeypatch.setattr(travel, "_archive", must_not_call)
    assert run(travel.fetch_climate(NAIROBI)) is bundled


def test_outbreaks_match_country_and_worldwide(monkeypatch):
    recent = str(TODAY - timedelta(days=10))
    old = str(TODAY - timedelta(days=400))
    news = [
        {"title": "Ebola disease - Democratic Republic of the Congo, Kenya and Nigeria", "date": recent, "url": "u1"},
        {"title": "Cholera - Kenya", "date": old, "url": "u2"},                      # too old
        {"title": "Yellow fever - Global", "date": recent, "url": "u3"},
        {"title": "Dengue - Brazil", "date": recent, "url": "u4"},
    ]

    async def fake_news():
        return news

    monkeypatch.setattr(travel, "_who_outbreak_news", fake_news)
    out = run(travel.fetch_outbreaks(NAIROBI))
    assert [o["url"] for o in out["country"]] == ["u1"]
    assert [o["url"] for o in out["worldwide"]] == ["u3"]


def test_congo_does_not_match_democratic_republic(monkeypatch):
    async def fake_news():
        return [{"title": "Mpox - Democratic Republic of the Congo", "date": str(TODAY), "url": "drc"}]

    monkeypatch.setattr(travel, "_who_outbreak_news", fake_news)
    congo = {**NAIROBI, "country": "Republic of the Congo", "country_code": "CG"}
    drc = {**NAIROBI, "country": "Democratic Republic of the Congo", "country_code": "CD"}
    assert run(travel.fetch_outbreaks(congo))["country"] == []
    assert len(run(travel.fetch_outbreaks(drc))["country"]) == 1


def test_cautions_are_plain_and_grouped():
    s = {
        "climate": {"months": [{"month": m, "avg_high": 31, "avg_low": 24, "rainy_days": 20,
                                "humidity": 85} for m in range(1, 13)]},
        "advisory": {"level": 2, "label": "Avoid all travel to parts of the country"},
        "outbreaks": {"country": [{"date": "2026-11-06", "title": "Ebola - Kenya"}], "worldwide": []},
        "health": {"malaria_per_1000": {"v": 74}, "basic_water_pct": {"v": 66}},
        "hazards": {"events": [
            {"alert": "Orange", "type": "Drought", "from": "2026-05-21", "name": "a"},
            {"alert": "Orange", "type": "Drought", "from": "2026-06-01", "name": "b"},
            {"alert": "Green", "type": "Flood", "from": "2026-07-01", "name": "c"},
        ]},
        "safety": {"road_deaths_100k": {"v": 35}},
    }
    holidays = [{"date": "2026-07-04", "name": "Some Day"}]
    text = " | ".join(travel.cautions(7, s, holidays))
    assert "Wet month: about 20 days with rain in July" in text
    assert "Hot and humid" in text
    assert "UK travel advice: avoid all travel to parts" in text
    assert "Ebola - Kenya" in text
    assert "Malaria risk" in text and "66% of people" in text
    assert text.count("drought") == 1 and "2 Orange drought alerts" in text   # grouped into one line
    assert "flood" not in text                                                # Green alerts are not flagged
    assert "Roads are dangerous" in text
    assert "Some Day (07-04)" in text


def test_cold_wet_month_mentions_snow():
    s = {"climate": {"months": [{"month": m, "avg_high": 1, "avg_low": -3, "rainy_days": 18,
                                 "humidity": 77} for m in range(1, 13)]}}
    text = " ".join(travel.cautions(1, s, []))
    assert "rain or snow" in text and "Cold" in text


def test_languages():
    assert travel.languages("KE") == ["Swahili", "English"]
