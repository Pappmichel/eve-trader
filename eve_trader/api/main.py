"""uvicorn entrypoint: `uvicorn eve_trader.api.main:app --reload`."""
from __future__ import annotations

from ..logging_setup import configure_logging
from .app import create_app

configure_logging()
app = create_app()
