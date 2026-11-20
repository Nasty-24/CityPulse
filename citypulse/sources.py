"""Data sources. Each fetcher takes a geocoded place and returns plain JSON data.

`place` is an Open-Meteo geocoding result: it has `id`, `latitude`, `longitude`,
`country_code`, `country`, `population`, `timezone` and more.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from babel.numbers import get_territory_currencies

from .cache import cached
from .labels import aqi_label, weather_label
from .upstream import get_json


class CityNotFound(Exception):
    def __init__(self, name, country_code=None):
        self.name, self.country_code = name, country_code
        super().__init__("city not found" + (f" in country {country_code}" if country_code else ""))


async def find_place(name: str, country_code: str | None = None):
    params = {"name": name, "count": 1}
    if country_code:
        params["countryCode"] = country_code
    j = await get_json("https://geocoding-api.open-meteo.com/v1/search", **params)
    if not j.get("results"):
        raise CityNotFound(name, country_code)
    return j["results"][0]


async def fetch_weather(place):
    j = await get_json("https://api.open-meteo.com/v1/forecast", latitude=place["latitude"],
                       longitude=place["longitude"], current="temperature_2m,wind_speed_10m,weather_code")
    current = j["current"]
    current["weather"] = weather_label(current.get("weather_code"))
    return current


async def fetch_forecast(place):
    j = await get_json(
        "https://api.open-meteo.com/v1/forecast", latitude=place["latitude"], longitude=place["longitude"],
        daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        forecast_days=3, timezone="auto")
    d = j["daily"]
    days = [{"date": d["time"][i], "high": d["temperature_2m_max"][i], "low": d["temperature_2m_min"][i],
             "rain_chance": d["precipitation_probability_max"][i],
             "weather": weather_label(d["weather_code"][i])} for i in range(len(d["time"]))]
    return {"days": days, "sunrise": d["sunrise"][0][-5:], "sunset": d["sunset"][0][-5:],
            "timezone": j["timezone"], "utc_offset_seconds": j["utc_offset_seconds"]}


async def fetch_air_quality(place):
    j = await get_json("https://air-quality-api.open-meteo.com/v1/air-quality", latitude=place["latitude"],
                       longitude=place["longitude"], current="us_aqi,pm2_5")
    current = j["current"]
    current["aqi_category"] = aqi_label(current.get("us_aqi"))
    return current


async def fetch_earthquakes(place):
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    j = await get_json("https://earthquake.usgs.gov/fdsnws/event/1/query", format="geojson",
                       latitude=place["latitude"], longitude=place["longitude"], maxradiuskm=300,
                       starttime=since, minmagnitude=2.5)
    magnitudes = [f["properties"]["mag"] for f in j["features"]]
    return {"n": len(magnitudes), "max": max(magnitudes, default=None)}


async def fetch_natural_events(place):
    lat, lon = place["latitude"], place["longitude"]
    # EONET bbox order: lon_min, lat_max, lon_max, lat_min
    j = await get_json("https://eonet.gsfc.nasa.gov/api/v3/events", status="open", days=30,
                       bbox=f"{lon - 3},{lat + 3},{lon + 3},{lat - 3}")
    events = j["events"]
    return {"n": len(events),
            "items": [{"t": e["title"], "cat": e["categories"][0]["title"]} for e in events[:5]]}


async def fetch_crime(place):
    cc = place["country_code"]
    j = await get_json(f"https://api.worldbank.org/v2/country/{cc}/indicator/VC.IHR.PSRC.P5",
                       format="json", mrnev=1)
    row = j[1][0] if len(j) > 1 and j[1] else None
    out = {"homicide_100k": {"v": row["value"], "yr": row["date"]} if row else None}
    if cc == "GB":  # street-level data only exists for the UK
        crimes = await get_json("https://data.police.uk/api/crimes-street/all-crime",
                                lat=place["latitude"], lng=place["longitude"])
        by_category = {}
        for c in crimes:
            by_category[c["category"]] = by_category.get(c["category"], 0) + 1
        out["local"] = {"total": len(crimes), "by_cat": by_category}
    return out


async def fetch_country(place):
    cc = place["country_code"]
    j = await get_json(f"https://api.worldbank.org/v2/country/{cc}", format="json")
    row = j[1][0]
    return {
        "cca3": row["id"],
        "name": row["name"],
        "capital": row["capitalCity"],
        "region": row["region"]["value"],
        "income": row["incomeLevel"]["value"],
        "currencies": {c: {} for c in get_territory_currencies(cc)},
    }


def country_info(place):
    """Country facts, shared through the cache by every source that needs them."""
    return cached(f"country:{place['country_code']}", 86400, lambda: fetch_country(place))


async def _fetch_usd_rates():
    # open.er-api.com: free, no key, ~160 currencies, refreshed daily
    j = await get_json("https://open.er-api.com/v6/latest/USD")
    if j.get("result") != "success":
        raise RuntimeError(f"fx upstream: {j.get('error-type', 'unknown error')}")
    date = datetime.fromtimestamp(j["time_last_update_unix"], timezone.utc).strftime("%Y-%m-%d")
    return {"rates": j["rates"], "date": date}


def usd_rates():
    # one shared table for all countries instead of one upstream call per country
    return cached("fx_table:USD", 3600, _fetch_usd_rates)


async def fetch_exchange_rate(place):
    country, _ = await country_info(place)
    currency = next(iter(country.get("currencies") or {}), None)
    if not currency:
        return None
    if currency == "USD":
        return {"cur": currency, "per_usd": 1.0}
    table, _ = await usd_rates()
    rate = table["rates"].get(currency)
    if rate is None:
        return {"cur": currency, "per_usd": None, "note": "unsupported"}
    return {"cur": currency, "per_usd": rate, "date": table["date"]}


# WHO Global Health Observatory indicators
VACCINE_INDICATORS = {"dtp3": "WHS4_100", "mcv1": "WHS8_110"}


async def fetch_vaccination(place):
    country, _ = await country_info(place)
    iso3 = country["cca3"]

    async def latest(key):
        j = await get_json(f"https://ghoapi.azureedge.net/api/{VACCINE_INDICATORS[key]}",
                           **{"$filter": f"SpatialDim eq '{iso3}'", "$orderby": "TimeDim desc", "$top": 1})
        rows = j["value"]
        return key, ({"v": rows[0]["NumericValue"], "yr": rows[0]["TimeDim"]} if rows else None)

    return dict(await asyncio.gather(*map(latest, VACCINE_INDICATORS)))


@dataclass(frozen=True)
class Source:
    ttl: int                                  # seconds a fetched value stays fresh
    fetch: Callable[[dict], Awaitable[object]]
    scope: str                                # "city" or "country": what the cache key is per

    def cache_key(self, name, place):
        return f"{name}:{place['country_code'] if self.scope == 'country' else place['id']}"


HOUR, DAY = 3600, 86400

SOURCES = {
    "weather":   Source(10 * 60, fetch_weather, "city"),
    "forecast":  Source(HOUR, fetch_forecast, "city"),
    "air":       Source(30 * 60, fetch_air_quality, "city"),
    "quakes":    Source(HOUR, fetch_earthquakes, "city"),
    "disasters": Source(30 * 60, fetch_natural_events, "city"),
    "crime":     Source(DAY, fetch_crime, "city"),
    "country":   Source(DAY, fetch_country, "country"),
    "fx":        Source(HOUR, fetch_exchange_rate, "country"),
    "vaccines":  Source(7 * DAY, fetch_vaccination, "country"),
}
