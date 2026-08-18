import pytest

from eve_trader import actions
from eve_trader.esi_client import ESIError


def test_pipeline_step_isolation_survives_esi_error_in_reconcile_trades(monkeypatch):
    # Confirmed real gap: do_reconcile_trades' step in do_pipeline only
    # caught ActionError, not ESIError - unlike every other step in the same
    # function - so an ESI failure (timeout, 420 rate limit) during
    # reconcile-trades crashed the whole pipeline with a raw 500 instead of
    # being isolated the same way build_universe/refresh_and_prune_candidates
    # already are.
    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", lambda safe=True: {"ok": True})

    def boom():
        raise ESIError("420 rate limited")
    monkeypatch.setattr(actions, "do_reconcile_trades", boom)

    result = actions.do_pipeline(safe=True)

    assert result["refresh_and_prune_candidates"] == {"ok": True}
    assert "error" in result["reconcile_trades"]


def test_pipeline_step_isolation_survives_action_error_in_reconcile_trades(monkeypatch):
    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", lambda safe=True: {"ok": True})

    def boom():
        raise actions.ActionError("buyer and seller both need to be logged in")
    monkeypatch.setattr(actions, "do_reconcile_trades", boom)

    result = actions.do_pipeline(safe=True)

    assert result["refresh_and_prune_candidates"] == {"ok": True}
    assert "error" in result["reconcile_trades"]
