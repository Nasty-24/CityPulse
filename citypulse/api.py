"""FastAPI app: routes for the dashboard, city snapshots and comparisons."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .cache import ROOT, cached
from .scoring import pulse_score
from .sources import SOURCES, CityNotFound, find_place

app = FastAPI(
    title="CityPulse",
    description="One request, one city, a full snapshot: weather, forecast, air quality, earthquakes, "
                "disasters, crime, currency and vaccination data from free public sources.",
    version=__version__,
)

SRC_HELP = f"Repeatable. Any of: {', '.join(SOURCES)}. Omit for all."


@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.exception_handler(CityNotFound)
async def city_not_found(_, e: CityNotFound):
    return JSONResponse({"detail": str(e)}, 404)


@app.exception_handler(httpx.HTTPError)
async def upstream_failed(_, e: httpx.HTTPError):
    try:
        url = str(e.request.url)
    except RuntimeError:
        url = None
    response = getattr(e, "response", None)
    return JSONResponse({"err": type(e).__name__,
                         "upstream": response.status_code if response is not None else None,
                         "url": url, "body": response.text[:200] if response is not None else str(e)}, 502)


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
            return src_name, {"data": None, "err": f"{type(e).__name__}: {e}"}

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
            return {"query": name, "err": f"lookup failed: {type(e).__name__}"}

    results = await asyncio.gather(*(one(n, cc) for n, cc in specs))
    ranking = sorted(
        ({"name": r["city"]["name"], "country": r["city"]["country"], "score": r["pulse"]["score"]}
         for r in results if "city" in r and r["pulse"]),
        key=lambda r: -r["score"],
    )
    return {"cities": results, "ranking": ranking}
