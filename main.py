"""Run with: uvicorn main:app --reload

The app lives in the citypulse package; this file keeps the original entry point working.
"""

from citypulse.api import app  # noqa: F401
