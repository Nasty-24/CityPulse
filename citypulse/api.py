"""FastAPI app: routes for the dashboard, city snapshots and comparisons."""

import asyncio
import calendar
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .cache import ROOT, cached, store
from .scoring import pulse_score
from .sources import SOURCES, CityNotFound, find_place
from .travel import DESTINATIONS, trip_report
from .upstream import client

log = logging.getLogger("citypulse")


@asynccontextmanager
async def lifespan(_app):
    yield
    await client.aclose()
    store.close()


app = FastAPI(
    title="CityPulse",
    description="One request, one city, a full snapshot: weather, forecast, air quality, earthquakes, "
                "disasters, crime, currency and vaccination data from free public sources.",
    version=__version__,
    lifespan=lifespan,
)


def public_error(e: Exception) -> str:
    """What callers see when a source fails. Details go to the server log only."""
    if isinstance(e, httpx.TimeoutException):
        return "source timed out"
    if isinstance(e, httpx.HTTPStatusError):
        return f"source returned HTTP {e.response.status_code}"
    if isinstance(e, httpx.HTTPError):
        return "source unreachable"
    return "source returned unexpected data"

SRC_HELP = f"Repeatable. Any of: {', '.join(SOURCES)}. Omit for all."


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.exception_handler(CityNotFound)
async def city_not_found(_, e: CityNotFound):
    return JSONResponse({"detail": str(e)}, 404)


@app.exception_handler(httpx.HTTPError)
async def upstream_failed(_, e: httpx.HTTPError):
    # only reached when the city lookup itself fails; per-source failures are handled in snapshot()
    log.warning("city lookup failed: %r", e)
    return JSONResponse({"detail": f"city lookup failed: {public_error(e)}"}, 502)


def check_sources(src):
    wanted = src or list(SOURCES)
    unknown = [s for s in wanted if s not in SOURCES]
    if unknown:
        raise HTTPException(400, f"unknown src: {unknown}")
    return wanted


async def locate(name: str, country_code: str | None):
    key = f"geo:{name.lower()}" + (f":{country_code}" if country_code else "")
    place, _ = await cached(key, 30 * 86400, lambda: find_place(name, country_code))
    return place


async def snapshot(name: str, country_code: str | None, wanted: list[str]):
    place = await locate(name, country_code)

    async def read(src_name):
        source = SOURCES[src_name]
        try:
            value, status = await cached(source.cache_key(src_name, place), source.ttl,
                                         lambda: source.fetch(place))
            return src_name, {"data": value, "cache": status}
        except Exception as e:
            log.warning("source %s failed for %s: %r", src_name, place.get("name"), e)
            return src_name, {"data": None, "err": public_error(e)}

    res = dict(await asyncio.gather(*(read(s) for s in wanted)))
    info = {k: place.get(k) for k in ("name", "country", "latitude", "longitude",
                                      "population", "elevation", "timezone")}
    # local time is computed per request, so it stays correct even when the forecast is cached
    forecast = (res.get("forecast") or {}).get("data") or {}
    if "utc_offset_seconds" in forecast:
        local = datetime.now(timezone.utc) + timedelta(seconds=forecast["utc_offset_seconds"])
        info["local_time"] = local.strftime("%Y-%m-%dT%H:%M")
    return {"city": info, "pulse": pulse_score(res), "res": res}


@app.get("/city/{name}")
async def city(
    name: str,
    src: list[str] = Query(None, description=SRC_HELP),
    country: str | None = Query(None, pattern="^[A-Za-z]{2}$",
                                description="ISO 3166-1 alpha-2 code to pick the right city, e.g. FR or US"),
):
    return await snapshot(name, country.upper() if country else None, check_sources(src))


def parse_city_spec(spec: str):
    """'Paris,US' -> ('Paris', 'US'); 'Rome' -> ('Rome', None)."""
    name, _, cc = spec.partition(",")
    name, cc = name.strip(), cc.strip().upper() or None
    if not name or (cc and not (len(cc) == 2 and cc.isalpha())):
        raise HTTPException(400, f'bad city "{spec}": use a name, optionally followed by ",XX" country code')
    return name, cc


@app.get("/compare")
async def compare(
    city: list[str] = Query(..., description='Repeat 2 to 5 times. Add a country code after a comma '
                                             'to pick the right one, e.g. "Paris,US".'),
    src: list[str] = Query(None, description=SRC_HELP),
):
    if not 2 <= len(city) <= 5:
        raise HTTPException(400, "compare needs between 2 and 5 cities")
    wanted = check_sources(src)
    specs = [parse_city_spec(c) for c in city]

    async def one(name, cc):
        try:
            return await snapshot(name, cc, wanted)
        except CityNotFound as e:
            return {"query": f"{name},{cc}" if cc else name, "err": str(e)}
        except httpx.HTTPError as e:
            log.warning("compare lookup failed for %s: %r", name, e)
            return {"query": name, "err": f"city lookup failed: {public_error(e)}"}

    results = await asyncio.gather(*(one(n, cc) for n, cc in specs))
    ranking = sorted(
        ({"name": r["city"]["name"], "country": r["city"]["country"], "score": r["pulse"]["score"]}
         for r in results if "city" in r and r["pulse"]),
        key=lambda r: -r["score"],
    )
    return {"cities": results, "ranking": ranking}


# ---------------------------------------------------------------- trip planning

def default_month():
    return date.today().month % 12 + 1  # next month


MONTH = Query(None, ge=1, le=12, description="Travel month, 1 to 12. Defaults to next month.")


def place_summary(place):
    return {k: place.get(k) for k in ("name", "country", "country_code", "latitude", "longitude",
                                      "population", "elevation", "timezone")}


@app.get("/trip/{name}")
async def trip(
    name: str,
    month: int | None = MONTH,
    country: str | None = Query(None, pattern="^[A-Za-z]{2}$", description="ISO 3166-1 alpha-2 code, e.g. FR"),
):
    """Everything a visitor should know about a city for a given month."""
    place = await locate(name, country.upper() if country else None)
    report = await trip_report(place, month or default_month())
    return {"city": place_summary(place), **report}


# limits how many destinations are researched at once, to stay polite to the free APIs
DESTINATION_CONCURRENCY = asyncio.Semaphore(4)


@app.get("/destinations")
async def destinations(
    month: int | None = MONTH,
    city: list[str] = Query(None, description='Optional shortlist to rank instead of the built-in list, '
                                              '2 to 12 entries like "Lisbon" or "Paris,FR".'),
    limit: int = Query(10, ge=1, le=30, description="How many destinations to return"),
):
    """Rank holiday destinations for a month by trip score, best first."""
    month = month or default_month()
    if city is not None and not 2 <= len(city) <= 12:
        raise HTTPException(400, "pass between 2 and 12 cities, or none for the built-in list")
    specs = [parse_city_spec(c) for c in city] if city else DESTINATIONS

    async def one(name, cc):
        async with DESTINATION_CONCURRENCY:
            try:
                place = await locate(name, cc)
                report = await trip_report(place, month)
            except CityNotFound as e:
                return {"query": name, "err": str(e)}
            except httpx.HTTPError as e:
                log.warning("destination lookup failed for %s: %r", name, e)
                return {"query": name, "err": f"city lookup failed: {public_error(e)}"}
        c = report["climate"] or {}
        return {
            "name": place["name"], "country": place["country"], "country_code": place["country_code"],
            "trip": report["trip"],
            "climate": {k: c.get(k) for k in ("avg_high", "avg_low", "rainy_days", "sunshine_hours")},
            "advisory_level": (report["safety"].get("advisory") or {}).get("level"),
            "price_level": ((report["costs"] or {}).get("price_level") or {}).get("label"),
            "top_caution": report["cautions"][0] if report["cautions"] else None,
            "best_months": [m["name"] for m in report["best_months"]],
        }

    results = await asyncio.gather(*(one(n, cc) for n, cc in specs))
    for r in results:
        if r.get("trip"):
            # without weather data a score says nothing about the month, so it ranks after full ones
            r["complete"] = "climate" in r["trip"]["parts"]
    ranked = sorted((r for r in results if r.get("trip")),
                    key=lambda r: (not r["complete"], -r["trip"]["score"]))
    return {"month": month, "month_name": calendar.month_name[month],
            "destinations": ranked[:limit],
            "not_ranked": [r for r in results if not r.get("trip")]}
