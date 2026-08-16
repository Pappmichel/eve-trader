import logging

from eve_trader import logging_setup


def test_configure_logging_attaches_console_and_file_handlers(monkeypatch):
    monkeypatch.setattr(logging_setup, "_configured", False)
    logger = logging.getLogger("eve_trader")
    for h in list(logger.handlers):
        logger.removeHandler(h)

    logging_setup.configure_logging()

    assert len(logger.handlers) == 2
    assert logger.propagate is False


def test_configure_logging_is_idempotent(monkeypatch):
    monkeypatch.setattr(logging_setup, "_configured", False)
    logger = logging.getLogger("eve_trader")
    for h in list(logger.handlers):
        logger.removeHandler(h)

    logging_setup.configure_logging()
    logging_setup.configure_logging()  # must not add a second pair of handlers

    assert len(logger.handlers) == 2


def test_configure_logging_does_not_touch_root_logger(monkeypatch):
    # Deliberately scoped to the "eve_trader" namespace, not the bare root -
    # must not interfere with uvicorn's own logging setup.
    monkeypatch.setattr(logging_setup, "_configured", False)
    logger = logging.getLogger("eve_trader")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    root_handlers_before = list(logging.getLogger().handlers)

    logging_setup.configure_logging()

    assert list(logging.getLogger().handlers) == root_handlers_before
