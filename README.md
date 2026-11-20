# CityPulse

A travel assistant that tells you **where and when to go**. For any city and month it brings together the weather you can expect, official travel warnings, crime and road safety, disease outbreaks and pandemics, malaria and drinking water, natural disaster alerts, costs and public holidays, all from free public sources. Built with FastAPI. No API keys needed.

```
GET /destinations?month=7          where to go in July, best first
GET /trip/Mombasa?month=7          everything to know about Mombasa in July
GET /city/Tokyo                    live conditions right now
```

## Why

Planning a holiday means checking a dozen sites: climate tables, government travel advice, WHO outbreak news, disaster alerts, health advice, exchange rates. CityPulse fans out to the relevant free sources, normalizes the results, scores them, and turns them into plain warnings like "Rainy season: about 23 days with rain in July" or "UK travel advice: avoid all travel to parts of the country".

Every figure says where it came from. See [docs/TRAVEL_DATA.md](docs/TRAVEL_DATA.md) for every aspect a traveller weighs, the source behind each, and what could be added next.

## Trip planning

- **Where to go**: `/destinations?month=7` ranks 30 popular destinations (or your own shortlist) for a month by trip score
- **Trip report**: `/trip/{city}?month=7` gives the trip score, the best months for weather, a "before you book" list of warnings, and the detail behind it
- **Weather by month**: average highs and lows, rainy days, rainfall, sunshine, humidity and daylight, from the last 3 years of daily history
- **Safety**: UK government travel advice level and latest change, homicide rate, road deaths, political stability
- **Health and pandemics**: WHO outbreak reports naming the country in the last 12 months, worldwide alerts, malaria, drinking water access, doctors per 1,000 people, vaccination coverage
- **Natural hazards**: GDACS cyclone, flood, drought, volcano and earthquake alerts from the last 90 days
- **Money and practical**: price level for visitors, tourist numbers, public holidays in your travel month, languages, elevation
- **Dashboard**: the web page at `/` asks "Where should you go in…?", ranks destinations, and shows each city's month-by-month weather chart

### Trip score

One 0 to 100 number for a city in a given month: weather 40%, safety 30%, health 18%, natural hazards 12%. Grades: 80+ Great time to go, 65+ Good time to go, 50+ Go with care, below 50 Think twice.

- An official warning against travel to the whole country caps safety at 35, so it can't be averaged away.
- A trip can't score more than 45 points above its weather score, so a freezing or monsoon month can't look great just because the country is safe.
- Cost is shown but not scored.

Full weights and thresholds: [docs/TRAVEL_DATA.md](docs/TRAVEL_DATA.md#how-the-trip-score-uses-this).

## City snapshot features

- 9 data sources behind one endpoint, including a 3-day forecast with sunrise and sunset
- Pick only the sources you need with `?src=`
- Independent cache per source, each with its own TTL
- Two tier cache: in-memory (L1) over SQLite (L2), survives restarts
- Stale-on-error: if a refresh fails, the last good value is served and flagged `stale`
- Per-key locking: concurrent identical requests cause one upstream call
- Partial results: a failed source returns `data: null` plus an error, the rest still come back
- Disambiguate cities with `?country=` (ISO2 code, e.g. `FR`, `US`)
- City facts with no extra calls: population, elevation, timezone and current local time
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
curl "localhost:8000/destinations?month=7"
curl "localhost:8000/trip/Lisbon?month=7"
curl localhost:8000/city/Tokyo
```

Dashboard: http://localhost:8000 | Interactive API docs: http://localhost:8000/docs

The first `/destinations` call after a start collects safety and health data for 30 countries and takes a minute or two; after that it is cached and fast.

### With Docker

```bash
docker build -t citypulse .
docker run -p 8000:8000 -v citypulse-cache:/data citypulse
```

The cache is kept on the `citypulse-cache` volume so it survives restarts. The container runs as a non-root user and has a health check.

### Tests

```bash
pip install pytest
python -m pytest
```

The tests run offline: every upstream API is replaced with fake data, so they are fast and never fail because a public API is down.

### Refreshing the bundled climate data

Monthly weather averages for the 30 built-in destinations ship in `citypulse/data/climate_normals.json`, which keeps `/destinations` fast and within Open-Meteo's free rate limits. To refresh them (takes a few minutes, by design):

```bash
python scripts/build_climate_normals.py
```

## API

### `GET /destinations`

| Param   | In    | Required | Description |
|---------|-------|----------|-------------|
| `month` | query | no       | 1 to 12. Defaults to next month. |
| `city`  | query | no       | Repeat 2 to 12 times to rank your own shortlist, e.g. `city=Lisbon&city=Paris,FR`. Omit for the 30 built-in destinations. |
| `limit` | query | no       | How many to return, 1 to 30. Default 10. |

Returns `destinations`, best first, each with its trip score, that month's weather, UK advisory level, price level, best months and top caution. Cities whose weather data is missing are marked `"complete": false` and ranked after complete ones. Cities that can't be found are listed in `not_ranked`.

### `GET /trip/{name}`

| Param     | In    | Required | Description |
|-----------|-------|----------|-------------|
| `name`    | path  | yes      | City name |
| `month`   | query | no       | 1 to 12. Defaults to next month. |
| `country` | query | no       | ISO 3166-1 alpha-2 code to pick the right city |

Returns `trip` (score, grade, parts), `best_months`, `cautions` (plain-language warnings), `climate` (that month) and `climate_by_month` (all 12, each with a weather score), `safety` (including `advisory`), `health` (including `outbreaks`), `hazards`, `costs`, `practical` (languages, public holidays that month) and `unavailable` (sections that could not be loaded). Every figure carries its `source` and year.

### `GET /city/{name}`

| Param  | In    | Required | Description                                                      |
|--------|-------|----------|------------------------------------------------------------------|
| `name`    | path  | yes      | City name. The first geocoding match is used.                    |
| `src`     | query | no       | Repeatable. One of `weather`, `forecast`, `air`, `quakes`, `disasters`, `crime`, `country`, `fx`, `vaccines`. Omitted means all of them.|
| `country` | query | no       | ISO 3166-1 alpha-2 code. Restricts the city lookup to that country, e.g. `?country=US` for Paris, Texas. |

Errors:

| Code | When                                         |
|------|----------------------------------------------|
| 400  | Unknown `src` value                          |
| 404  | City not found (in `country`, if given)      |
| 422  | `country` is not a two-letter code           |
| 502  | City lookup failed upstream (`detail` says how) |

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
    "longitude": 139.69171,
    "population": 8336599,
    "elevation": 44.0,
    "timezone": "Asia/Tokyo",
    "local_time": "2026-11-21T06:30"
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
      "err": "source timed out"
    }
  }
}
```

Each entry in `res` has:

| Field   | Meaning                                                              |
|---------|----------------------------------------------------------------------|
| `data`  | Normalized payload, or `null` on failure                             |
| `cache` | `hit` (fresh cache), `miss` (just fetched), `stale` (refresh failed, old value served) |
| `err`   | Present only when `data` is `null`. A short reason, such as `source timed out`. |

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

`val` is the JSON payload, `exp` is the expiry as a unix timestamp. The file is `cache.db` in the project root (override with the `CITYPULSE_CACHE_DB` environment variable). WAL mode is on, and SQLite calls run in a worker thread so they never block the event loop. The in-memory tier keeps at most 5,000 entries, dropping the least recently used first; per-key locks are released as soon as no request needs them.

## Project layout

```
main.py                 entry point for `uvicorn main:app`
citypulse/api.py        FastAPI app and routes
citypulse/sources.py    one fetcher per data source, plus the SOURCES registry
citypulse/cache.py      two-tier cache
citypulse/scoring.py    pulse score and shared scoring helpers
citypulse/travel.py     trip planning: monthly climate, advisories, outbreaks, hazards, health, costs, trip score
citypulse/data/         bundled climate averages for the built-in destinations
citypulse/labels.py     weather and AQI labels
citypulse/upstream.py   shared HTTP client
static/index.html       web dashboard (trip planner) served at /
scripts/                maintenance scripts, e.g. rebuilding climate data
tests/                  offline test suite
docs/TRAVEL_DATA.md     every travel aspect and its data source
cache.db                created at runtime
```

Adding a source takes 2 steps:

1. Write `async def fetch_something(place)` in `citypulse/sources.py`, where `place` is the geocoding result (`id`, `latitude`, `longitude`, `country_code`, ...), and return a JSON serializable value
2. Register it in `SOURCES`: `"something": Source(ttl_seconds, fetch_something, "city")`, or `"country"` to cache once per country

Sources that need country facts (like `fx`) call `country_info(place)`, which reads the shared `country` cache entry.

## HTTP client notes

- `httpx.AsyncClient` with a 15 s total and 10 s connect timeout, closed cleanly on shutdown
- `AsyncHTTPTransport(retries=2, local_address="0.0.0.0")` retries connection failures and forces IPv4
- Send an honest `User-Agent` (set in `citypulse/upstream.py`). Several providers rate limit or block anonymous or spoofed agents.
- A custom transport makes httpx ignore proxy environment variables. If you are behind a proxy, pass `proxy=os.environ.get("HTTPS_PROXY")` to the transport.
- Errors shown to callers are short and generic (`source timed out`, `source returned HTTP 503`, ...). Full details, including upstream URLs, go to the server log only.

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
- No auth and no rate limiting on the API itself
- Disaster data (EONET) lists major events only, so an empty list is common
