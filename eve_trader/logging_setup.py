"""Shared logging setup for the API server entrypoint (api/main.py) - a
console handler plus a rotating file handler under data/, so the app leaves
an inspectable trail even when nobody's watching the terminal at the time
(most relevant now that scheduler.py can run job attempts in the background
for hours between anyone opening a browser tab).

Scoped to the "eve_trader" logger namespace specifically (every logger in
this package is a child of it, e.g. "eve_trader.scheduler",
"eve_trader.actions") rather than the bare root logger - deliberately does
NOT touch uvicorn's own loggers ("uvicorn", "uvicorn.access"), which already
have their own working console setup; attaching to the root logger the way
cli.py's plain logging.basicConfig does would risk doubling or reformatting
uvicorn's own log lines.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import DATA_DIR

LOG_PATH = DATA_DIR / "eve_trader.log"
_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent - safe to call more than once (e.g. under --reload, which
    re-executes module-level code) without piling up duplicate handlers and
    duplicating every log line."""
    global _configured
    if _configured:
        return
    _configured = True

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    # 5MB x 3 backups - generous for a normal day of request/scheduler
    # activity, capped so an always-on background process can't grow this
    # file without bound over weeks of uptime.
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger = logging.getLogger("eve_trader")
    logger.setLevel(level)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger.propagate = False  # don't also duplicate through the bare root logger
