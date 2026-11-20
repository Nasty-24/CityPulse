# CityPulse

One request, one city, a full snapshot: weather, air quality, earthquakes, natural disasters, crime, currency vs USD, and vaccination coverage. Built with FastAPI. No API keys needed.

```
GET /city/Tokyo
GET /city/Nairobi?src=weather&src=fx
GET /city/Paris?country=US
```

## Why

Public data APIs are scattered, inconsistent, and flaky. CityPulse fans out to the relevant free sources, normalizes the results, and caches every source independently, so one failing provider never forces a refetch of the others.

## Features

- 8 data sources behind one endpoint
- Pick only the sources you need with `?src=`
- Independent cache per source, each with its own TTL
- Two tier cache: in-memory (L1) over SQLite (L2), survives restarts
- Stale-on-error: if a refresh fails, the last good value is served and flagged `stale`
- Per-key locking: concurrent identical requests cause one upstream call
- Partial results: a failed source returns `data: null` plus an error, the rest still come back
- Disambiguate cities with `?country=` (ISO2 code, e.g. `FR`, `US`)
- Human-readable labels: weather description and AQI category alongside the raw values
- Exchange rates for ~160 currencies
- Zero API keys
- Web dashboard at `/`: search a city and see every reading, with a pulse line that spikes on notable values
- Pulse score: one 0 to 100 number per city, with a per-reading breakdown
- Compare 2 to 5 cities side by side with `/compare`, ranked by pulse score
- Auto generated docs at `/docs` (Swagger) and `/redoc`

## Quick start

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn httpx babel
uvicorn main:app --reload
```

Then:

```bash
curl localhost:8000/city/Tokyo
curl "localhost:8000/city/Nairobi?src=weather&src=air"
```

Dashboard: http://localhost:8000 | Interactive API docs: http://localhost:8000/docs

## API

### `GET /city/{name}`

| Param  | In    | Required | Description                                                      |
|--------|-------|----------|------------------------------------------------------------------|
| `name`    | path  | yes      | City name. The first geocoding match is used.                    |
| `src`     | query | no       | Repeatable. One of `weather`, `air`, `quakes`, `disasters`, `crime`, `country`, `fx`, `vaccines`. Omitted means all of them.|
| `country` | query | no       | ISO 3166-1 alpha-2 code. Restricts the city lookup to that country, e.g. `?country=US` for Paris, Texas. |

Errors:

| Code | When                                         |
|------|----------------------------------------------|
| 400  | Unknown `src` value                          |
| 404  | City not found (in `country`, if given)      |
| 422  | `country` is not a two-letter code           |
| 502  | Geocoding upstream failed (see body for why) |

Per source failures do not fail the request. They appear inside `res`.

### `GET /compare`

```
GET /compare?city=Nairobi&city=Kampala
GET /compare?city=Paris,US&city=Paris,FR&src=air&src=weather
```

| Param  | In    | Required | Description                                                         |
|--------|-------|----------|---------------------------------------------------------------------|
| `city` | query | yes      | Repeat 2 to 5 times. Add `,XX` for a country code, e.g. `Paris,US`. |
| `src`  | query | no       | Same as for `/city`.                                                |

Returns `cities` (one `/city` style snapshot per city, in the order asked) and `ranking` (cities sorted by pulse score, best first). A city that cannot be found does not fail the request: its entry is `{"query": ..., "err": ...}` and it is left out of the ranking. Returns 400 for fewer than 2 or more than 5 cities, or a malformed country code.

The dashboard has the same view: open a city and use "Compare with", or go to `/?compare=Nairobi&compare=Kampala`.

### Response

```json
{
  "city": {
    "name": "Tokyo",
    "country": "Japan",
    "latitude": 35.6895,
    "longitude": 139.69171
  },
  "res": {
    "weather": {
      "data": { "time": "2026-10-30T23:00", "interval": 900, "temperature_2m": 14.9, "wind_speed_10m": 5.0, "weather_code": 0, "weather": "Clear sky" },
      "cache": "miss"
    },
    "air": {
      "data": { "time": "2026-10-30T23:00", "interval": 3600, "us_aqi": 151, "pm2_5": 54.8, "aqi_category": "Unhealthy" },
      "cache": "hit"
    },
    "quakes": {
      "data": { "n": 19, "max": 5.5 },
      "cache": "miss"
    },
    "crime": {
      "data": { "homicide_100k": { "v": 0.229, "yr": "2023" } },
      "cache": "hit"
    },
    "fx": {
      "data": null,
      "err": "ConnectTimeout: "
    }
  }
}
```

Each entry in `res` has:

| Field   | Meaning                                                              |
|---------|----------------------------------------------------------------------|
| `data`  | Normalized payload, or `null` on failure                             |
| `cache` | `hit` (fresh cache), `miss` (just fetched), `stale` (refresh failed, old value served) |
| `err`   | Present only when `data` is `null`. Exception type and message.      |

Weather code values follow the WMO weather interpretation codes used by Open-Meteo; `weather` is the matching description. `aqi_category` uses the US EPA bands (Good, Moderate, Unhealthy for sensitive groups, Unhealthy, Very unhealthy, Hazardous).

Exchange rates come from [open.er-api.com](https://www.exchangerate-api.com/docs/free) (free, no key, updated daily). One USD rate table is fetched and cached for all countries.

## Pulse score

Every response includes a `pulse` object: one 0 to 100 number (higher is better) that sums up how the city is doing.

```json
"pulse": {
  "score": 76,
  "grade": "Steady",
  "parts": { "air": 79, "weather": 40, "quakes": 48, "disasters": 100, "crime": 99, "vaccines": 92 },
  "based_on": "6 of 6 readings"
}
```

| Part        | Weight | 100 means                     | 0 means                         |
|-------------|--------|-------------------------------|---------------------------------|
| `air`       | 25     | AQI 0                         | AQI 300 or worse                |
| `crime`     | 20     | No homicides                  | 25+ homicides per 100k          |
| `weather`   | 15     | 21 °C, no storms              | 25 °C away from 21, or storms lower it by 30 |
| `quakes`    | 15     | Nothing above M4 in 30 days   | M8 or stronger                  |
| `vaccines`  | 15     | 100% DTP3 and MCV1 coverage   | 0% coverage                     |
| `disasters` | 10     | No active events nearby       | 5 or more active events         |

Grades: 80+ Thriving, 60+ Steady, 40+ Strained, below 40 Under stress. Missing or failed readings are left out and the weights are rebalanced over the rest; `based_on` says how many were used. If nothing is available, `pulse` is `null`. The weights are a judgment call, not a scientific index, and live in `PULSE_WEIGHTS` in `main.py`.

## How caching works

```
request -> L1 memory -> L2 SQLite -> upstream
                 ^           ^           |
                 +-----------+-----------+  write back on success
```

- Cache key: `{src}:{id}` where `id` is the geocoded city id, or the ISO2 country code for country scoped sources
- Fresh entry in L1 or L2: return it (`hit`)
- Expired or missing: take the key lock, recheck, fetch, store in both tiers (`miss`)
- Fetch fails and an expired entry exists: return it (`stale`)
- Fetch fails and nothing cached: the source reports an error, other sources are unaffected
- Failures are never cached, so the next request retries only the failed source
- Startup purges rows that expired more than 7 days ago. Fresher expired rows are kept on purpose as stale fallbacks.

SQLite schema:

```sql
create table c(k text primary key, exp real, val text)
```

`val` is the JSON payload, `exp` is the expiry as a unix timestamp. The file is `cache.db`, created next to `main.py`. WAL mode is on.

## Project layout

```
main.py            app, cache, fetchers, source registry, endpoints
static/index.html  web dashboard served at /
cache.db           created at runtime
```

Adding a source takes 2 steps:

1. Write `async def fn(g)` where `g` is the geocoding result (`id`, `latitude`, `longitude`, `country_code`, ...) and return a JSON serializable value
2. Register it: `S["name"] = (ttl_seconds, fn, "city")` or `"cc"` for country scope

Sources that need country info (like `fx`) call `cnc(g)`, which reads the shared `country` cache entry.

## HTTP client notes

- `httpx.AsyncClient` with a 15 s total and 5 s connect timeout
- `AsyncHTTPTransport(retries=2, local_address="0.0.0.0")` retries connection failures and forces IPv4
- Send an honest `User-Agent` such as `citypulse/0.1 (you@example.com)`. Several providers rate limit or block anonymous or spoofed agents.
- A custom transport makes httpx ignore proxy environment variables. If you are behind a proxy, pass `proxy=os.environ.get("HTTPS_PROXY")` to the transport.
- Upstream `httpx` errors outside the per source wrapper (for example geocoding) return a 502 with `err`, `upstream`, `url`, and `body`.

## Verify before relying on

Upstream APIs change. These items were not confirmed against live responses in every environment:

- WHO GHO host (`ghoapi.azureedge.net`) and indicator codes `WHS4_100` (DTP3) and `WHS8_110` (MCV1). List codes with `GET /api/Indicator?$filter=contains(IndicatorName,'DTP3')`.
- EONET `bbox` parameter order: `lon_min,lat_max,lon_max,lat_min`
- World Bank homicide indicator `VC.IHR.PSRC.P5` has gaps for many countries and lags by 1 to 2 years

REST Countries v1 to v4 are retired and v5 needs an account and key, so this project does not use it.

## Limitations

- Crime is country level homicide rate only, except street level counts for UK cities. There is no free global city level crime source.
- Geocoding takes the first match. "Paris" resolves to whatever the geocoder ranks first; pass `?country=` to narrow it.
- `fx` rates update once a day, so they are not suitable for trading
- L1 cache is per process. Multiple workers share L2 via SQLite and may duplicate some fetches.
- SQLite calls are synchronous on the event loop. Fine at hackathon scale, wrap in `asyncio.to_thread` or use `aiosqlite` for more load.
- No auth and no rate limiting on the API itself
- Disaster data (EONET) lists major events only, so an empty list is common
