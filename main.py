import asyncio, time
from datetime import datetime, timedelta, timezone
import httpx
from fastapi import FastAPI, HTTPException, Query
from pathlib import Path
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(
    title="CityPulse",
    description="One request, one city, a full snapshot: weather, air quality, earthquakes, "
                "disasters, crime, currency and vaccination data from 8 free sources.",
    version="0.1.0",
)

HERE = Path(__file__).parent

@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(HERE / "static" / "index.html")
cl = httpx.AsyncClient(
    timeout=httpx.Timeout(15, connect=10),
    transport=httpx.AsyncHTTPTransport(retries=2, local_address="0.0.0.0"),
    headers={"user-agent": "citypulse/0.1 (hackathon)"},
)

# cl = httpx.AsyncClient(
#     timeout=httpx.Timeout(15, connect=5),
#     transport=httpx.AsyncHTTPTransport(retries=2),
#     headers={"user-agent": "curl"},
# )
C, L = {}, {}

import json, sqlite3

db = sqlite3.connect(HERE / "cache.db", check_same_thread=False)
db.execute("pragma journal_mode=wal")
db.execute("create table if not exists c(k text primary key, exp real, val text)")
db.execute("delete from c where exp < ?", (time.time() - 7 * 86400,))
db.commit()

def dget(k):
    r = db.execute("select exp,val from c where k=?", (k,)).fetchone()
    return (r[0], json.loads(r[1])) if r else None

def dset(k, exp, v):
    db.execute("insert or replace into c values(?,?,?)", (k, exp, json.dumps(v)))
    db.commit()

def look(k):
    e = C.get(k)
    if not e:
        e = dget(k)
        if e:
            C[k] = e
    return e

async def cached(k, ttl, fn):
    e = look(k)
    if e and e[0] > time.time():
        return e[1], "hit"
    async with L.setdefault(k, asyncio.Lock()):
        e = look(k)
        if e and e[0] > time.time():
            return e[1], "hit"
        try:
            v = await fn()
        except Exception:
            if e:
                return e[1], "stale"
            raise
        x = time.time() + ttl
        C[k] = (x, v)
        dset(k, x, v)
        return v, "miss"

async def get(u, **p):
    r = await cl.get(u, params=p)
    r.raise_for_status()
    return r.json()

from babel.numbers import get_territory_currencies

async def cn(g):
    cc = g["country_code"]
    j = await get(f"https://api.worldbank.org/v2/country/{cc}", format="json")
    r = j[1][0]
    return {
        "cca3": r["id"],
        "name": r["name"],
        "capital": r["capitalCity"],
        "region": r["region"]["value"],
        "income": r["incomeLevel"]["value"],
        "currencies": {c: {} for c in get_territory_currencies(cc)},
    }

def cnc(g):
    return cached(f"country:{g['country_code']}", 86400, lambda: cn(g))

async def fx(g):
    c, _ = await cnc(g)
    cur = next(iter(c.get("currencies") or {}), None)
    if not cur:
        return None
    if cur == "USD":
        return {"cur": cur, "per_usd": 1.0}
    j, _ = await usd_rates()
    rate = j["rates"].get(cur)
    if rate is None:
        return {"cur": cur, "per_usd": None, "note": "unsupported"}
    return {"cur": cur, "per_usd": rate, "date": j["date"]}

async def _usd_rates():
    # open.er-api.com: free, no key, ~160 currencies, refreshed daily
    j = await get("https://open.er-api.com/v6/latest/USD")
    if j.get("result") != "success":
        raise RuntimeError(f"fx upstream: {j.get('error-type', 'unknown error')}")
    d = datetime.fromtimestamp(j["time_last_update_unix"], timezone.utc).strftime("%Y-%m-%d")
    return {"rates": j["rates"], "date": d}

def usd_rates():
    # one shared table for all countries instead of one upstream call per country
    return cached("fx_table:USD", 3600, _usd_rates)

async def ds(g):
    la, lo = g["latitude"], g["longitude"]
    j = await get("https://eonet.gsfc.nasa.gov/api/v3/events", status="open", days=30,
                  bbox=f"{lo-3},{la+3},{lo+3},{la-3}")
    e = j["events"]
    return {"n": len(e), "items": [{"t": x["title"], "cat": x["categories"][0]["title"]} for x in e[:5]]}

async def cr(g):
    cc = g["country_code"]
    j = await get(f"https://api.worldbank.org/v2/country/{cc}/indicator/VC.IHR.PSRC.P5",
                  format="json", mrnev=1)
    r = j[1][0] if len(j) > 1 and j[1] else None
    out = {"homicide_100k": {"v": r["value"], "yr": r["date"]} if r else None}
    if cc == "GB":
        c = await get("https://data.police.uk/api/crimes-street/all-crime",
                      lat=g["latitude"], lng=g["longitude"])
        n = {}
        for x in c:
            n[x["category"]] = n.get(x["category"], 0) + 1
        out["local"] = {"total": len(c), "by_cat": n}
    return out

V = {"dtp3": "WHS4_100", "mcv1": "WHS8_110"}

async def vac(g):
    c, _ = await cnc(g)
    i3 = c["cca3"]

    async def one(k):
        j = await get(f"https://ghoapi.azureedge.net/api/{V[k]}",
                      **{"$filter": f"SpatialDim eq '{i3}'", "$orderby": "TimeDim desc", "$top": 1})
        r = j["value"]
        return k, ({"v": r[0]["NumericValue"], "yr": r[0]["TimeDim"]} if r else None)

    return dict(await asyncio.gather(*map(one, V)))

async def geo(n, cc=None):
    p = {"name": n, "count": 1}
    if cc:
        p["countryCode"] = cc
    j = await get("https://geocoding-api.open-meteo.com/v1/search", **p)
    if not j.get("results"):
        raise HTTPException(404, "city not found" + (f" in country {cc}" if cc else ""))
    return j["results"][0]

async def wx(g):
    j = await get("https://api.open-meteo.com/v1/forecast", latitude=g["latitude"],
                  longitude=g["longitude"], current="temperature_2m,wind_speed_10m,weather_code")
    c = j["current"]
    c["weather"] = WMO.get(c.get("weather_code"), "Unknown")
    return c

# WMO weather interpretation codes, as used by Open-Meteo
WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

# US EPA AQI bands: (upper bound, label)
AQI = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for sensitive groups"),
       (200, "Unhealthy"), (300, "Very unhealthy")]

def aqi_label(v):
    if v is None:
        return None
    return next((name for hi, name in AQI if v <= hi), "Hazardous")

async def aq(g):
    j = await get("https://air-quality-api.open-meteo.com/v1/air-quality", latitude=g["latitude"],
                  longitude=g["longitude"], current="us_aqi,pm2_5")
    c = j["current"]
    c["aqi_category"] = aqi_label(c.get("us_aqi"))
    return c

async def eq(g):
    t = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    j = await get("https://earthquake.usgs.gov/fdsnws/event/1/query", format="geojson",
                  latitude=g["latitude"], longitude=g["longitude"], maxradiuskm=300,
                  starttime=t, minmagnitude=2.5)
    m = [f["properties"]["mag"] for f in j["features"]]
    return {"n": len(m), "max": max(m, default=None)}

def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))

# Pulse score: each reading becomes a 0-100 sub-score (higher = better),
# then a weighted average over the readings that are available.
PULSE_WEIGHTS = {"air": 25, "crime": 20, "weather": 15, "quakes": 15, "vaccines": 15, "disasters": 10}
PULSE_GRADES = [(80, "Thriving"), (60, "Steady"), (40, "Strained"), (0, "Under stress")]

def pulse_parts(res):
    d = {k: v.get("data") for k, v in res.items()}
    parts = {}
    if (a := d.get("air")) and a.get("us_aqi") is not None:
        parts["air"] = clamp(100 - a["us_aqi"] / 3)  # AQI 0 -> 100, 150 -> 50, 300+ -> 0
    if (w := d.get("weather")) and w.get("temperature_2m") is not None:
        s = 100 - abs(w["temperature_2m"] - 21) * 4  # comfort peaks at 21 C
        code = w.get("weather_code") or 0
        if code in (65, 67, 75, 82, 86) or code >= 95:  # heavy rain/snow, storms
            s -= 30
        parts["weather"] = clamp(s)
    if (q := d.get("quakes")) is not None:
        m = q.get("max") or 0
        parts["quakes"] = clamp(100 - max(0, m - 4) * 25)  # under M4 is harmless
    if (x := d.get("disasters")) is not None:
        parts["disasters"] = clamp(100 - x["n"] * 20)
    if (c := d.get("crime")) and c.get("homicide_100k"):
        parts["crime"] = clamp(100 - c["homicide_100k"]["v"] * 4)  # 25 per 100k -> 0
    if v := d.get("vaccines"):
        vals = [v[k]["v"] for k in ("dtp3", "mcv1") if v.get(k)]
        if vals:
            parts["vaccines"] = clamp(sum(vals) / len(vals))
    return parts

def pulse_score(res):
    parts = pulse_parts(res)
    if not parts:
        return None
    w = sum(PULSE_WEIGHTS[k] for k in parts)
    score = round(sum(parts[k] * PULSE_WEIGHTS[k] for k in parts) / w)
    grade = next(g for lo, g in PULSE_GRADES if score >= lo)
    return {"score": score, "grade": grade,
            "parts": {k: round(v) for k, v in parts.items()},
            "based_on": f"{len(parts)} of {len(PULSE_WEIGHTS)} readings"}

S = {
    "weather": (600, wx, "city"),
    "air": (1800, aq, "city"),
    "quakes": (3600, eq, "city"),
    "disasters": (1800, ds, "city"),
    "crime": (86400, cr, "city"),
    "country": (86400, cn, "cc"),
    "fx": (3600, fx, "cc"),
    "vaccines": (86400 * 7, vac, "cc"),
}

@app.exception_handler(httpx.HTTPError)
async def he(_, e):
    try:
        u = str(e.request.url)
    except RuntimeError:
        u = None
    r = getattr(e, "response", None)
    return JSONResponse({"err": type(e).__name__, "upstream": r.status_code if r is not None else None,
                         "url": u, "body": r.text[:200] if r is not None else str(e)}, 502)

@app.get("/city/{name}")
async def city(
    name: str,
    src: list[str] = Query(None, description=f"Repeatable. Any of: {', '.join(S)}. Omit for all."),
    country: str | None = Query(None, pattern="^[A-Za-z]{2}$",
                                description="ISO 3166-1 alpha-2 code to pick the right city, e.g. FR or US"),
):
    want = src or list(S)
    bad = [s for s in want if s not in S]
    if bad:
        raise HTTPException(400, f"unknown src: {bad}")
    cc = country.upper() if country else None
    gk = f"geo:{name.lower()}" + (f":{cc}" if cc else "")
    g, _ = await cached(gk, 30 * 86400, lambda: geo(name, cc))

    async def one(s):
        ttl, fn, sc = S[s]
        k = g["country_code"] if sc == "cc" else g["id"]
        try:
            v, st = await cached(f"{s}:{k}", ttl, lambda: fn(g))
            return s, {"data": v, "cache": st}
        except Exception as e:
            return s, {"data": None, "err": f"{type(e).__name__}: {e}"}

    res = dict(await asyncio.gather(*(one(s) for s in want)))
    return {"city": {k: g.get(k) for k in ("name", "country", "latitude", "longitude")},
            "pulse": pulse_score(res), "res": res}
