"""Shared HTTP client for all upstream data sources."""

import httpx

USER_AGENT = "citypulse/0.2 (+https://github.com/Nasty-24/CityPulse)"

client = httpx.AsyncClient(
    timeout=httpx.Timeout(15, connect=10),
    # retries cover connection failures only; local_address forces IPv4,
    # which avoids slow IPv6 fallbacks on some networks
    transport=httpx.AsyncHTTPTransport(retries=2, local_address="0.0.0.0"),
    headers={"user-agent": USER_AGENT},
)


async def get_json(url: str, **params):
    """GET a URL and return its JSON body, raising on HTTP errors."""
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()
