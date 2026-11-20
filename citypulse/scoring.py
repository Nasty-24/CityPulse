"""Pulse score: each reading becomes a 0-100 sub-score (higher is better),
then a weighted average over the readings that are available."""

from .labels import SEVERE_WEATHER

PULSE_WEIGHTS = {"air": 25, "crime": 20, "weather": 15, "quakes": 15, "vaccines": 15, "disasters": 10}
GRADES = [(80, "Thriving"), (60, "Steady"), (40, "Strained"), (0, "Under stress")]


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def air_score(aqi):
    return clamp(100 - aqi / 3)  # AQI 0 -> 100, 150 -> 50, 300+ -> 0


def temperature_comfort(celsius, ideal=21):
    return clamp(100 - abs(celsius - ideal) * 4)  # 25 degrees away from ideal -> 0


def quake_score(max_magnitude):
    return clamp(100 - max(0, (max_magnitude or 0) - 4) * 25)  # under M4 is harmless


def natural_events_score(count):
    return clamp(100 - count * 20)


def homicide_score(per_100k):
    return clamp(100 - per_100k * 4)  # 25 per 100k -> 0


def grade(score):
    return next(name for lowest, name in GRADES if score >= lowest)


def weighted(parts, weights):
    """Weighted average of the parts present; None if nothing is available."""
    if not parts:
        return None
    total = sum(weights[k] for k in parts)
    return round(sum(parts[k] * weights[k] for k in parts) / total)


def pulse_parts(res):
    data = {k: v.get("data") for k, v in res.items()}
    parts = {}
    if (air := data.get("air")) and air.get("us_aqi") is not None:
        parts["air"] = air_score(air["us_aqi"])
    if (weather := data.get("weather")) and weather.get("temperature_2m") is not None:
        s = temperature_comfort(weather["temperature_2m"])
        if (weather.get("weather_code") or 0) in SEVERE_WEATHER:
            s -= 30
        parts["weather"] = clamp(s)
    if (quakes := data.get("quakes")) is not None:
        parts["quakes"] = quake_score(quakes.get("max"))
    if (events := data.get("disasters")) is not None:
        parts["disasters"] = natural_events_score(events["n"])
    if (crime := data.get("crime")) and crime.get("homicide_100k"):
        parts["crime"] = homicide_score(crime["homicide_100k"]["v"])
    if vaccines := data.get("vaccines"):
        values = [vaccines[k]["v"] for k in ("dtp3", "mcv1") if vaccines.get(k)]
        if values:
            parts["vaccines"] = clamp(sum(values) / len(values))
    return parts


def pulse_score(res):
    parts = pulse_parts(res)
    score = weighted(parts, PULSE_WEIGHTS)
    if score is None:
        return None
    return {"score": score, "grade": grade(score),
            "parts": {k: round(v) for k, v in parts.items()},
            "based_on": f"{len(parts)} of {len(PULSE_WEIGHTS)} readings"}
