"""Trip planning: what a city is like in a given month, and how safe, healthy and
affordable it is for visitors. Every figure comes from a free public source,
named in SOURCES_USED so travellers can check it themselves.
"""

import asyncio
import calendar
import json
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from babel import Locale
from babel.languages import get_official_languages

from .cache import cached
from .scoring import clamp, grade, homicide_score, weighted
from .sources import country_info, fetch_crime, fetch_vaccination
from .upstream import client, get_json

log = logging.getLogger("citypulse")
HOUR, DAY = 3600, 86400


# ---------------------------------------------------------------- shared helpers

async def world_bank_latest(cc, indicator, source=None):
    """Most recent non-empty value of a World Bank indicator: (value, year) or (None, None)."""
    params = {"format": "json", "mrnev": 1}
    if source:
        params["source"] = source
    j = await get_json(f"https://api.worldbank.org/v2/country/{cc}/indicator/{indicator}", **params)
    row = j[1][0] if len(j) > 1 and j[1] else None
    return (row["value"], row["date"]) if row else (None, None)


async def who_gho_latest(indicator, iso3, extra=""):
    """Most recent value from the WHO Global Health Observatory: (value, year) or (None, None)."""
    j = await get_json(f"https://ghoapi.azureedge.net/api/{indicator}",
                       **{"$filter": f"SpatialDim eq '{iso3}'{extra}", "$orderby": "TimeDim desc", "$top": 1})
    rows = j["value"]
    return (rows[0]["NumericValue"], rows[0]["TimeDim"]) if rows else (None, None)


def fact(value, year, source):
    return None if value is None else {"v": round(value, 2), "yr": year, "source": source}


# names used by WHO and GDACS differ from the geocoder's for a few countries
COUNTRY_ALIASES = {
    "US": ["United States of America", "United States", "USA"],
    "GB": ["United Kingdom"],
    "CD": ["Democratic Republic of the Congo"],
    "CG": ["Republic of the Congo"],
    "KR": ["Republic of Korea", "South Korea"],
    "KP": ["Democratic People's Republic of Korea"],
    "TR": ["Türkiye", "Turkey"],
    "IR": ["Iran (Islamic Republic of)", "Iran"],
    "VN": ["Viet Nam", "Vietnam"],
    "RU": ["Russian Federation", "Russia"],
    "TZ": ["United Republic of Tanzania", "Tanzania"],
    "LA": ["Lao People's Democratic Republic", "Laos"],
    "BO": ["Bolivia (Plurinational State of)", "Bolivia"],
    "VE": ["Venezuela (Bolivarian Republic of)", "Venezuela"],
    "CI": ["Côte d'Ivoire", "Cote d'Ivoire", "Ivory Coast"],
    "CZ": ["Czechia", "Czech Republic"],
}


def country_names(place):
    names = {place["country"], *COUNTRY_ALIASES.get(place["country_code"], [])}
    return [n for n in names if n]


# ---------------------------------------------------------------- climate by month

def daylight_hours(latitude, month):
    """Hours of daylight on the 15th of the month (standard sunrise equation)."""
    day_of_year = date(2025, month, 15).timetuple().tm_yday
    declination = math.radians(23.44) * math.sin(2 * math.pi * (284 + day_of_year) / 365)
    x = -math.tan(math.radians(latitude)) * math.tan(declination)
    return round(24 * math.acos(max(-1.0, min(1.0, x))) / math.pi, 1)


CLIMATE_YEARS = 3
# The archive API weighs a multi-year request as many calls, so the free per-minute
# limit is easy to hit. Keep few in flight and wait out "429 Too Many Requests".
CLIMATE_CONCURRENCY = asyncio.Semaphore(2)
CLIMATE_FILE = Path(__file__).parent / "data" / "climate_normals.json"


def _load_bundled_climate():
    try:
        return json.loads(CLIMATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


BUNDLED_CLIMATE = _load_bundled_climate()  # {"lat,lon": normals}, built by scripts/build_climate_normals.py


def climate_key(place):
    return f"{place['latitude']:.2f},{place['longitude']:.2f}"


async def _archive(place, start, end):
    for attempt in range(3):
        async with CLIMATE_CONCURRENCY:
            r = await client.get("https://archive-api.open-meteo.com/v1/archive", params={
                "latitude": place["latitude"], "longitude": place["longitude"],
                "start_date": start, "end_date": end, "timezone": "auto",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                         "sunshine_duration,relative_humidity_2m_mean"})
        if r.status_code != 429 or attempt == 2:
            r.raise_for_status()
            return r.json()
        await asyncio.sleep(20 * (attempt + 1))


async def fetch_climate(place, use_bundled=True):
    """Monthly climate normals from recent full years of daily history (Open-Meteo / ERA5)."""
    if use_bundled and climate_key(place) in BUNDLED_CLIMATE:
        return BUNDLED_CLIMATE[climate_key(place)]
    this_year = datetime.now(timezone.utc).year
    years = CLIMATE_YEARS
    j = await _archive(place, f"{this_year - years}-01-01", f"{this_year - 1}-12-31")
    d = j["daily"]
    months = {m: {"hi": [], "lo": [], "rain": [], "sun": [], "hum": []} for m in range(1, 13)}
    for i, day in enumerate(d["time"]):
        bucket = months[int(day[5:7])]
        for key, series in (("hi", "temperature_2m_max"), ("lo", "temperature_2m_min"),
                            ("rain", "precipitation_sum"), ("sun", "sunshine_duration"),
                            ("hum", "relative_humidity_2m_mean")):
            value = d[series][i]
            if value is not None:
                bucket[key].append(value)

    def mean(values):
        return round(sum(values) / len(values), 1) if values else None

    normals = []
    for m in range(1, 13):
        b = months[m]
        normals.append({
            "month": m,
            "avg_high": mean(b["hi"]),
            "avg_low": mean(b["lo"]),
            "rain_mm": round(sum(b["rain"]) / years) if b["rain"] else None,
            "rainy_days": round(sum(1 for r in b["rain"] if r >= 1) / years, 1) if b["rain"] else None,
            "sunshine_hours": round(mean(b["sun"]) / 3600, 1) if b["sun"] else None,
            "humidity": mean(b["hum"]),
            "daylight_hours": daylight_hours(place["latitude"], m),
        })
    return {"months": normals, "years": f"{this_year - years}-{this_year - 1}",
            "source": "Open-Meteo historical weather (ERA5)"}


def climate_score(n):
    """0-100 for a holiday: warm and dry with sunshine scores high."""
    if not n or n.get("avg_high") is None:
        return None
    warmth = clamp(100 - abs(n["avg_high"] - 26) * 4)          # ~26 C afternoons are ideal
    dryness = clamp(100 - (n.get("rainy_days") or 0) * 5)        # 20 rainy days -> 0
    sun = clamp((n.get("sunshine_hours") or 0) / 9 * 100)        # 9+ sunny hours/day -> 100
    muggy = max(0, (n.get("humidity") or 0) - 75) * 1.5 if n["avg_high"] > 28 else 0
    # dry, sunny days only count as much as it is warm: a sunny 2 C day is still no holiday weather
    pleasant = (0.3 * dryness + 0.2 * sun) * (0.4 + 0.6 * warmth / 100)
    return round(clamp(0.5 * warmth + pleasant - muggy))


# ---------------------------------------------------------------- safety

ADVISORY_LEVELS = {
    "avoid_all_but_essential_travel_to_parts": (1, "Avoid all but essential travel to parts of the country"),
    "avoid_all_travel_to_parts": (2, "Avoid all travel to parts of the country"),
    "avoid_all_but_essential_travel_to_whole_country": (3, "Avoid all but essential travel to the whole country"),
    "avoid_all_travel_to_whole_country": (4, "Avoid all travel to the whole country"),
}
ADVISORY_SCORE = [100, 75, 55, 20, 0]


async def _fcdo_index():
    j = await get_json("https://www.gov.uk/api/content/foreign-travel-advice")
    index = {}
    for child in j["links"]["children"]:
        country = child.get("details", {}).get("country") or {}
        for name in [country.get("name"), *country.get("synonyms", [])]:
            if name:
                index[name.lower()] = country["slug"]
    return index


async def fetch_advisory(place):
    """UK government (FCDO) travel advice: the most widely published official warning levels."""
    if place["country_code"] == "GB":
        return {"level": 0, "label": "Home country of the advice publisher; no warnings issued",
                "source": "UK FCDO"}
    index, _ = await cached("fcdo_index", DAY, _fcdo_index)
    slug = next((index[n.lower()] for n in country_names(place) if n.lower() in index), None)
    if not slug:
        return None
    j = await get_json(f"https://www.gov.uk/api/content/foreign-travel-advice/{slug}")
    statuses = j["details"].get("alert_status") or []
    level, label = max((ADVISORY_LEVELS.get(s, (0, "")) for s in statuses), default=(0, ""))
    return {"level": level, "label": label or "No travel warnings; check the advice before you go",
            "latest_change": j["details"].get("change_description"),
            "updated": (j.get("public_updated_at") or "")[:10],
            "url": f"https://www.gov.uk/foreign-travel-advice/{slug}", "source": "UK FCDO travel advice"}


async def fetch_safety(place):
    country, _ = await country_info(place)
    cc, iso3 = place["country_code"], country["cca3"]
    crime, road, stability = await asyncio.gather(
        fetch_crime(place),
        who_gho_latest("RS_198", iso3, " and Dim1 eq 'SEX_BTSX'"),
        world_bank_latest(cc, "GOV_WGI_PV.EST", source=3),
        return_exceptions=True)
    out = {}
    if not isinstance(crime, Exception) and crime.get("homicide_100k"):
        h = crime["homicide_100k"]
        out["homicide_100k"] = fact(h["v"], h["yr"], "World Bank (UNODC)")
    if not isinstance(road, Exception):
        out["road_deaths_100k"] = fact(*road, "WHO Global Health Observatory")
    if not isinstance(stability, Exception):
        out["political_stability"] = fact(*stability, "World Bank Worldwide Governance Indicators")
    return out


# ---------------------------------------------------------------- health

async def _who_outbreak_news():
    j = await get_json("https://www.who.int/api/news/diseaseoutbreaknews",
                       **{"$orderby": "PublicationDate desc", "$top": 100,
                          "$select": "Title,PublicationDate,UrlName"})
    out, seen = [], set()
    for x in j["value"]:  # newest first, so the first of each title is its latest update
        if x["Title"] in seen:
            continue
        seen.add(x["Title"])
        out.append({"title": x["Title"], "date": x["PublicationDate"][:10],
                    "url": f"https://www.who.int/emergencies/disease-outbreak-news/item/{x['UrlName']}"})
    return out


WORLDWIDE = re.compile(r"\b(global|multi-country|worldwide|pandemic)\b", re.I)


async def fetch_outbreaks(place):
    """WHO Disease Outbreak News from the last 12 months that name this country, plus worldwide ones."""
    news, _ = await cached("who_don", 6 * HOUR, _who_outbreak_news)
    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
    names = country_names(place)
    pattern = re.compile("|".join(rf"(?<!Democratic )\b{re.escape(n)}\b" for n in names), re.I)
    recent = [x for x in news if x["date"] >= since]
    local = [x for x in recent if pattern.search(x["title"])]
    worldwide = [x for x in recent if WORLDWIDE.search(x["title"]) and x not in local]
    return {"country": local[:8], "worldwide": worldwide[:5], "source": "WHO Disease Outbreak News"}


async def fetch_health(place):
    country, _ = await country_info(place)
    cc, iso3 = place["country_code"], country["cca3"]
    malaria, water, doctors, vaccines = await asyncio.gather(
        who_gho_latest("MALARIA_EST_INCIDENCE", iso3),
        world_bank_latest(cc, "SH.H2O.BASW.ZS"),
        world_bank_latest(cc, "SH.MED.PHYS.ZS"),
        fetch_vaccination(place),
        return_exceptions=True)
    out = {}
    if not isinstance(malaria, Exception):
        # WHO reports nothing for countries without local transmission
        out["malaria_per_1000"] = fact(*malaria, "WHO Global Health Observatory") or \
            {"v": 0, "yr": None, "source": "WHO (no local transmission reported)"}
    if not isinstance(water, Exception):
        out["basic_water_pct"] = fact(*water, "World Bank (WHO/UNICEF JMP)")
    if not isinstance(doctors, Exception):
        out["physicians_per_1000"] = fact(*doctors, "World Bank (WHO)")
    if not isinstance(vaccines, Exception):
        out["vaccines"] = vaccines
    return out


# ---------------------------------------------------------------- hazards

GDACS_TYPES = {"EQ": "Earthquake", "TC": "Tropical cyclone", "FL": "Flood", "VO": "Volcano",
               "DR": "Drought", "WF": "Wildfire", "TS": "Tsunami"}


async def fetch_hazards(place):
    """GDACS (UN/EU) disaster alerts for the country over the last 90 days."""
    today = datetime.now(timezone.utc).date()
    for name in country_names(place):
        r = await client.get("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
                             params={"country": name, "fromdate": str(today - timedelta(days=90)),
                                     "todate": str(today)})
        if r.status_code == 204 or not r.content:
            continue
        r.raise_for_status()
        features = r.json().get("features", [])
        if features:
            break
    else:
        features = []
    events = [{"type": GDACS_TYPES.get(p["eventtype"], p["eventtype"]), "alert": p["alertlevel"],
               "name": p.get("name") or p.get("eventname"), "from": (p.get("fromdate") or "")[:10],
               "url": (p.get("url") or {}).get("report")}
              for p in (f["properties"] for f in features)]
    order = {"Red": 0, "Orange": 1, "Green": 2}
    events.sort(key=lambda e: (order.get(e["alert"], 3), e["from"]))
    return {"events": events[:10], "red": sum(e["alert"] == "Red" for e in events),
            "orange": sum(e["alert"] == "Orange" for e in events), "source": "GDACS (UN / European Commission)"}


# ---------------------------------------------------------------- money and practical

async def fetch_costs(place):
    cc = place["country_code"]
    (ppp, year), (rate, _), (arrivals, arr_year) = await asyncio.gather(
        world_bank_latest(cc, "PA.NUS.PPP"), world_bank_latest(cc, "PA.NUS.FCRF"),
        world_bank_latest(cc, "ST.INT.ARVL"))
    out = {}
    if ppp and rate:
        level = ppp / rate  # what a dollar of US prices costs locally; below 1 means cheaper than the US
        label = ("Very cheap" if level < 0.4 else "Cheap" if level < 0.6 else "Moderate" if level < 0.85
                 else "Expensive" if level < 1.1 else "Very expensive")
        out["price_level"] = {"v": round(level, 2), "yr": year, "label": label,
                              "source": "World Bank (PPP conversion factor / exchange rate), US = 1"}
    if arrivals:
        out["tourist_arrivals"] = fact(arrivals, arr_year, "World Bank (UN Tourism)")
    return out


async def fetch_holidays(cc, year):
    r = await client.get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{cc}")
    if r.status_code in (204, 404) or not r.content:
        return None  # country not covered by Nager.Date
    r.raise_for_status()
    return [{"date": h["date"], "name": h["name"], "local_name": h["localName"]} for h in r.json()]


def languages(cc):
    names = []
    for code in get_official_languages(cc):
        try:
            names.append(Locale.parse(code).get_display_name("en"))
        except Exception:
            names.append(code)
    return names


# ---------------------------------------------------------------- trip report

SECTIONS = {
    # name: (ttl, fetcher, cache scope)
    "climate":   (30 * DAY, fetch_climate, "city"),
    "safety":    (7 * DAY, fetch_safety, "country"),
    "advisory":  (6 * HOUR, fetch_advisory, "country"),
    "health":    (7 * DAY, fetch_health, "country"),
    "outbreaks": (6 * HOUR, fetch_outbreaks, "country"),
    "hazards":   (6 * HOUR, fetch_hazards, "country"),
    "costs":     (30 * DAY, fetch_costs, "country"),
}
TRIP_WEIGHTS = {"climate": 40, "safety": 30, "health": 18, "hazards": 12}
CLIMATE_CAP = 45  # a holiday can't rate much better than its weather: score <= climate + 45
TRIP_GRADES = [(80, "Great time to go"), (65, "Good time to go"), (50, "Go with care"), (0, "Think twice")]


async def load_sections(place, names=SECTIONS):
    async def one(name):
        ttl, fetch, scope = SECTIONS[name]
        key = f"trip_{name}:{place['country_code'] if scope == 'country' else place['id']}"
        try:
            value, _ = await cached(key, ttl, lambda: fetch(place))
            return name, value
        except Exception as e:
            log.warning("trip section %s failed for %s: %r", name, place.get("name"), e)
            return name, None
    return dict(await asyncio.gather(*(one(n) for n in names)))


def safety_score(safety, advisory):
    parts, weights = {}, {"advisory": 40, "homicide": 30, "stability": 15, "roads": 15}
    if advisory:
        parts["advisory"] = ADVISORY_SCORE[advisory["level"]]
    if safety and safety.get("homicide_100k"):
        parts["homicide"] = homicide_score(safety["homicide_100k"]["v"])
    if safety and safety.get("political_stability"):
        parts["stability"] = clamp((safety["political_stability"]["v"] + 2.5) / 5 * 100)
    if safety and safety.get("road_deaths_100k"):
        parts["roads"] = clamp(100 - safety["road_deaths_100k"]["v"] * 3)  # ~33 per 100k -> 0
    score = weighted(parts, weights)
    # an official "avoid all travel" warning should never be averaged away
    if advisory and advisory["level"] >= 3 and score is not None:
        score = min(score, 35)
    return score


def health_score(health, outbreaks):
    parts, weights = {}, {"outbreaks": 35, "malaria": 25, "water": 20, "doctors": 10, "vaccines": 10}
    if outbreaks is not None:
        parts["outbreaks"] = clamp(100 - 30 * len(outbreaks["country"]))
    if health:
        if health.get("malaria_per_1000"):
            parts["malaria"] = clamp(100 - health["malaria_per_1000"]["v"] / 2)  # 200 per 1000 -> 0
        if health.get("basic_water_pct"):
            parts["water"] = health["basic_water_pct"]["v"]
        if health.get("physicians_per_1000"):
            parts["doctors"] = clamp(health["physicians_per_1000"]["v"] / 2.5 * 100)
        vac = health.get("vaccines") or {}
        values = [vac[k]["v"] for k in ("dtp3", "mcv1") if vac.get(k)]
        if values:
            parts["vaccines"] = sum(values) / len(values)
    return weighted(parts, weights)


def hazards_score(hazards):
    if hazards is None:
        return None
    return round(clamp(100 - 40 * hazards["red"] - 20 * hazards["orange"]))


def trip_score(month, s):
    normals = (s.get("climate") or {}).get("months") or []
    parts = {
        "climate": climate_score(normals[month - 1]) if normals else None,
        "safety": safety_score(s.get("safety"), s.get("advisory")),
        "health": health_score(s.get("health"), s.get("outbreaks")),
        "hazards": hazards_score(s.get("hazards")),
    }
    parts = {k: v for k, v in parts.items() if v is not None}
    score = weighted(parts, TRIP_WEIGHTS)
    if score is None:
        return None
    if "climate" in parts:
        score = min(score, round(parts["climate"]) + CLIMATE_CAP)
    return {"score": score, "grade": next(g for lo, g in TRIP_GRADES if score >= lo),
            "parts": {k: round(v) for k, v in parts.items()},
            "based_on": f"{len(parts)} of {len(TRIP_WEIGHTS)} areas"}


def cautions(month, s, holidays_in_month):
    """Plain-language warnings and tips a traveller should read before booking."""
    out = []
    n = ((s.get("climate") or {}).get("months") or [None] * 12)[month - 1]
    mname = calendar.month_name[month]
    if n:
        if (n.get("rainy_days") or 0) >= 12:
            wet = "rain or snow" if (n.get("avg_low") or 99) <= 1 else "rain"
            out.append(f"Wet month: about {n['rainy_days']:.0f} days with {wet} in {mname} on average.")
        if (n.get("avg_high") or 0) >= 33:
            out.append(f"Very hot: afternoons average {n['avg_high']:.0f}°C in {mname}.")
        elif (n.get("avg_high") or 99) <= 8:
            out.append(f"Cold: afternoons average only {n['avg_high']:.0f}°C in {mname}.")
        if (n.get("humidity") or 0) >= 80 and (n.get("avg_high") or 0) >= 28:
            out.append(f"Hot and humid: {n['humidity']:.0f}% average humidity.")
    adv = s.get("advisory")
    if adv and adv["level"] >= 1:
        out.append(f"UK travel advice: {adv['label'].lower()}. Check which areas before booking.")
    for o in ((s.get("outbreaks") or {}).get("country") or [])[:3]:
        out.append(f"WHO outbreak report ({o['date']}): {o['title']}.")
    health = s.get("health") or {}
    if (health.get("malaria_per_1000") or {}).get("v", 0) >= 1:
        out.append("Malaria risk: ask a travel clinic about prevention tablets and use repellent.")
    water = (health.get("basic_water_pct") or {}).get("v")
    if water is not None and water < 90:
        out.append(f"Water: only {water:.0f}% of people have basic drinking water; drink bottled or treated water.")
    hz = s.get("hazards") or {}
    grouped = {}  # one line per alert level and hazard type, e.g. "2 Orange drought alerts"
    for e in hz.get("events", []):
        if e["alert"] in ("Red", "Orange"):
            grouped.setdefault((e["alert"], e["type"]), []).append(e)
    for (alert, kind), events in sorted(grouped.items()):
        first = min(e["from"] for e in events)
        what = f"{len(events)} {alert} {kind.lower()} alerts" if len(events) > 1 else f"{alert} {kind.lower()} alert"
        out.append(f"{what} for this country since {first} (GDACS). Check conditions near your destination.")
    safety = s.get("safety") or {}
    if (safety.get("road_deaths_100k") or {}).get("v", 0) >= 20:
        out.append("Roads are dangerous: road deaths are high, so prefer trusted drivers over self-drive.")
    if holidays_in_month:
        names = ", ".join(f"{h['name']} ({h['date'][5:]})" for h in holidays_in_month[:4])
        out.append(f"Public holidays in {mname}: {names}. Expect crowds, closures and higher prices.")
    return out


def best_months(climate, top=3):
    normals = (climate or {}).get("months") or []
    scored = [(climate_score(n), n["month"]) for n in normals]
    scored = sorted((x for x in scored if x[0] is not None), reverse=True)[:top]
    return [{"month": m, "name": calendar.month_name[m], "climate_score": sc} for sc, m in scored]


async def trip_report(place, month):
    s = await load_sections(place)
    year = date.today().year if month >= date.today().month else date.today().year + 1
    try:
        holidays, _ = await cached(f"holidays:{place['country_code']}:{year}", 30 * DAY,
                                   lambda: fetch_holidays(place["country_code"], year))
    except Exception as e:
        log.warning("holidays failed for %s: %r", place["country_code"], e)
        holidays = None
    in_month = [h for h in (holidays or []) if int(h["date"][5:7]) == month]
    climate = s.get("climate")
    normals = (climate or {}).get("months") or []
    return {
        "month": month, "month_name": calendar.month_name[month], "year": year,
        "trip": trip_score(month, s),
        "best_months": best_months(climate),
        "cautions": cautions(month, s, in_month),
        "climate": {**normals[month - 1], "years": climate["years"], "source": climate["source"]}
                   if normals else None,
        "climate_by_month": [{**n, "score": climate_score(n)} for n in normals] or None,
        "safety": {**(s.get("safety") or {}), "advisory": s.get("advisory")},
        "health": {**(s.get("health") or {}), "outbreaks": s.get("outbreaks")},
        "hazards": s.get("hazards"),
        "costs": s.get("costs"),
        "practical": {
            "languages": languages(place["country_code"]),
            "holidays_in_month": in_month if holidays is not None else None,
            "holidays_source": "Nager.Date" if holidays is not None else "not available for this country",
        },
        "unavailable": [k for k, v in s.items() if v is None],
    }


# ---------------------------------------------------------------- where to go

# Popular holiday destinations across regions and budgets; override with ?city=
DESTINATIONS = [
    ("Barcelona", "ES"), ("Lisbon", "PT"), ("Rome", "IT"), ("Athens", "GR"), ("Dubrovnik", "HR"),
    ("Paris", "FR"), ("Reykjavik", "IS"), ("Istanbul", "TR"), ("Marrakesh", "MA"), ("Cairo", "EG"),
    ("Cape Town", "ZA"), ("Zanzibar", "TZ"), ("Mombasa", "KE"), ("Kigali", "RW"), ("Dubai", "AE"),
    ("Bangkok", "TH"), ("Denpasar", "ID"), ("Hanoi", "VN"), ("Tokyo", "JP"), ("Seoul", "KR"),
    ("Singapore", "SG"), ("Sydney", "AU"), ("Queenstown", "NZ"), ("Cancun", "MX"), ("New York", "US"),
    ("Vancouver", "CA"), ("Rio de Janeiro", "BR"), ("Cusco", "PE"), ("Buenos Aires", "AR"), ("Havana", "CU"),
]
