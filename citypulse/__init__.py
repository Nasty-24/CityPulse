"""CityPulse: live city snapshots and trip planning from free public data."""

__version__ = "0.2.0"


def main() -> None:
    """Entry point for the `citypulse` command: serve the API and dashboard."""
    import uvicorn

    uvicorn.run("citypulse.api:app", host="127.0.0.1", port=8000)
