"""Rebuild citypulse/data/climate_normals.json for the built-in destinations.

Monthly climate normals change slowly, so shipping them with the app makes the
/destinations ranking fast and keeps it under Open-Meteo's free rate limits.

Run from the project root:  python scripts/build_climate_normals.py
It is slow on purpose (a pause between cities) to stay within the free limits.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from citypulse.sources import find_place  # noqa: E402
from citypulse.travel import CLIMATE_FILE, DESTINATIONS, climate_key, fetch_climate  # noqa: E402
from citypulse.upstream import client  # noqa: E402

PAUSE_SECONDS = 8


async def main():
    out = {}
    for i, (name, cc) in enumerate(DESTINATIONS, 1):
        place = await find_place(name, cc)
        out[climate_key(place)] = await fetch_climate(place, use_bundled=False)
        print(f"[{i}/{len(DESTINATIONS)}] {name}, {cc}", flush=True)
        await asyncio.sleep(PAUSE_SECONDS)
    CLIMATE_FILE.parent.mkdir(exist_ok=True)
    CLIMATE_FILE.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {len(out)} cities to {CLIMATE_FILE.relative_to(ROOT)}")
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
