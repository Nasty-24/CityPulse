from citypulse.labels import aqi_label, weather_label
from citypulse.scoring import grade, pulse_score
from citypulse.travel import (CLIMATE_CAP, best_months, climate_score, daylight_hours,
                              safety_score, trip_score)


def wrap(**data):
    return {k: {"data": v} for k, v in data.items()}


CALM = wrap(air={"us_aqi": 10}, weather={"temperature_2m": 21, "weather_code": 0},
            quakes={"n": 0, "max": None}, disasters={"n": 0, "items": []},
            crime={"homicide_100k": {"v": 0.5, "yr": "2022"}},
            vaccines={"dtp3": {"v": 98, "yr": 2024}, "mcv1": {"v": 96, "yr": 2024}})


def test_labels():
    assert weather_label(3) == "Overcast"
    assert weather_label(12345) == "Unknown"
    assert [aqi_label(v) for v in (0, 50, 51, 151, 400)] == \
        ["Good", "Good", "Moderate", "Unhealthy", "Hazardous"]
    assert aqi_label(None) is None


def test_pulse_calm_city_thrives():
    p = pulse_score(CALM)
    assert p["score"] >= 95 and p["grade"] == "Thriving"
    assert p["based_on"] == "6 of 6 readings"


def test_pulse_stressed_city():
    p = pulse_score(wrap(air={"us_aqi": 280}, weather={"temperature_2m": 41, "weather_code": 95},
                         quakes={"n": 9, "max": 6.8}, disasters={"n": 4, "items": []},
                         crime={"homicide_100k": {"v": 30, "yr": "2022"}}))
    assert p["score"] < 40 and p["grade"] == "Under stress"


def test_pulse_uses_only_available_readings():
    p = pulse_score({**wrap(air={"us_aqi": 50}), "weather": {"data": None, "err": "source timed out"}})
    assert p["based_on"] == "1 of 6 readings"
    assert pulse_score({"air": {"data": None}}) is None


def test_grades():
    assert [grade(s) for s in (100, 80, 79, 60, 40, 0)] == \
        ["Thriving", "Thriving", "Steady", "Steady", "Strained", "Under stress"]


def month(**kw):
    base = {"month": 7, "avg_high": 26, "avg_low": 18, "rainy_days": 2, "sunshine_hours": 9, "humidity": 60}
    return {**base, **kw}


def test_climate_score_prefers_warm_dry_sunny():
    ideal = climate_score(month())
    assert ideal >= 95
    assert climate_score(month(avg_high=2)) < 40          # freezing
    assert climate_score(month(rainy_days=25)) < ideal - 25  # monsoon
    assert climate_score(month(avg_high=33, humidity=90)) < climate_score(month(avg_high=33, humidity=50))
    assert climate_score({"avg_high": None}) is None


def test_daylight_hours():
    assert 11.9 <= daylight_hours(0, 6) <= 12.3          # equator: ~12 h all year
    assert daylight_hours(64, 6) > 20                    # Reykjavik in June
    assert daylight_hours(64, 12) < 5                    # Reykjavik in December
    assert daylight_hours(-34, 6) < daylight_hours(-34, 12)  # southern hemisphere flips


def test_whole_country_warning_caps_safety():
    safe_numbers = {"homicide_100k": {"v": 0.5}, "political_stability": {"v": 1.5}, "road_deaths_100k": {"v": 3}}
    assert safety_score(safe_numbers, {"level": 0}) > 90
    assert safety_score(safe_numbers, {"level": 3}) <= 35


def sections(high):
    return {"climate": {"months": [month(month=m, avg_high=high) for m in range(1, 13)]},
            "safety": {"homicide_100k": {"v": 0.5}}, "advisory": {"level": 0},
            "health": {"basic_water_pct": {"v": 100}}, "outbreaks": {"country": [], "worldwide": []},
            "hazards": {"events": [], "red": 0, "orange": 0}}


def test_bad_weather_caps_trip_score():
    t = trip_score(1, sections(high=0))
    assert t["parts"]["climate"] < 30
    assert t["score"] <= t["parts"]["climate"] + CLIMATE_CAP
    assert t["grade"] in ("Go with care", "Think twice")


def test_trip_score_great_month():
    t = trip_score(7, sections(high=26))
    assert t["score"] >= 90 and t["grade"] == "Great time to go"
    assert t["based_on"] == "4 of 4 areas"


def test_best_months():
    # highs of 11 C in January rising to 22 C in December: the warmest months come closest to 26 C
    climate = {"months": [month(month=m, avg_high=10 + m) for m in range(1, 13)]}
    assert [b["name"] for b in best_months(climate)] == ["December", "November", "October"]
    assert best_months(None) == []
