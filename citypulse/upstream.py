"""Shared HTTP client for all upstream data sources.

Free public APIs drop connections and slow down under load, so every request goes
through `fetch`: at most PER_HOST requests run against one host at a time, and
dropped connections, timeouts and temporary server errors are retried with backoff.
"""

import asyncio
from collections import defaultdict
from urllib.parse import urlparse

import httpx

USER_AGENT = "citypulse/0.2 (+https://github.com/Nasty-24/CityPulse)"
PER_HOST = 6           # concurrent requests per upstream host
RETRIES = 2            # extra attempts after the first
BACKOFF_SECONDS = 1.5  # doubled on each retry
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_ERRORS = (httpx.TransportError,)  # connect/read errors, timeouts, dropped connections

client = httpx.AsyncClient(
    timeout=httpx.Timeout(20, connect=10),
    # local_address forces IPv4, which avoids slow IPv6 fallbacks on some networks
    transport=httpx.AsyncHTTPTransport(retries=1, local_address="0.0.0.0"),
    headers={"user-agent": USER_AGENT},
)

_host_limits: dict = defaultdict(lambda: asyncio.Semaphore(PER_HOST))


async def fetch(url: str, **params) -> httpx.Response:
    """GET with a per-host concurrency cap and retries. Raises after the last attempt fails."""
    limit = _host_limits[urlparse(url).netloc]
    for attempt in range(RETRIES + 1):
        last = attempt == RETRIES
        try:
            async with limit:
                response = await client.get(url, params=params)
        except RETRY_ERRORS:
            if last:
                raise
        else:
            if response.status_code not in RETRY_STATUS or last:
                return response
        await asyncio.sleep(BACKOFF_SECONDS * 2 ** attempt)


async def get_json(url: str, **params):
    """GET a URL and return its JSON body, raising on HTTP errors."""
    response = await fetch(url, **params)
    response.raise_for_status()
    return response.json()
