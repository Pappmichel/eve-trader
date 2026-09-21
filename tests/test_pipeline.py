from eve_trader import actions
from eve_trader.esi_client import ESIError


def test_pipeline_step_isolation_survives_esi_error_in_reconcile_trades(monkeypatch):
    # Confirmed real gap: do_reconcile_trades' step in do_pipeline only
    # caught ActionError, not ESIError - unlike every other step in the same
    # function - so an ESI failure (timeout, 420 rate limit) during
    # reconcile-trades crashed the whole pipeline with a raw 500 instead of
    # being isolated the same way build_universe/refresh_and_prune_candidates
    # already are.
    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", lambda safe=True, progress_callback=None: {"ok": True})

    def boom():
        raise ESIError("420 rate limited")
    monkeypatch.setattr(actions, "do_reconcile_trades", boom)

    result = actions.do_pipeline(safe=True)

    assert result["refresh_and_prune_candidates"] == {"ok": True}
    assert "error" in result["reconcile_trades"]
    assert result["failed_steps"]["reconcile_trades"] == "420 rate limited"
    assert actions.pipeline_outcome(result) == "degraded"


def test_pipeline_step_isolation_survives_action_error_in_reconcile_trades(monkeypatch):
    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", lambda safe=True, progress_callback=None: {"ok": True})

    def boom():
        raise actions.ActionError("buyer and seller both need to be logged in")
    monkeypatch.setattr(actions, "do_reconcile_trades", boom)

    result = actions.do_pipeline(safe=True)

    assert result["refresh_and_prune_candidates"] == {"ok": True}
    assert "error" in result["reconcile_trades"]
    assert actions.pipeline_outcome(result) == "degraded"


def test_pipeline_refresh_failure_still_runs_reconcile_and_reports_failed_steps(monkeypatch):
    ran = []

    def boom(safe=True, progress_callback=None):
        raise actions.ActionError("shortlist ESI failed")

    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", boom)
    monkeypatch.setattr(
        actions, "do_reconcile_trades",
        lambda: ran.append("reconcile") or {"matched_trades": 0},
    )

    result = actions.do_pipeline(safe=True)

    assert ran == ["reconcile"]
    assert result["failed_steps"]["refresh_and_prune_candidates"] == "shortlist ESI failed"
    assert result["reconcile_trades"] == {"matched_trades": 0}
    assert actions.pipeline_outcome(result) == "degraded"


def test_pipeline_full_success_has_no_failed_steps(monkeypatch):
    monkeypatch.setattr(
        actions, "do_refresh_and_prune_candidates",
        lambda safe=True, progress_callback=None: {"ok": True},
    )
    monkeypatch.setattr(actions, "do_reconcile_trades", lambda: {"ok": True})

    result = actions.do_pipeline(safe=True)

    assert "failed_steps" not in result
    assert result["refresh_and_prune_candidates"] == {"ok": True}
    assert result["reconcile_trades"] == {"ok": True}
    assert actions.pipeline_outcome(result) == "succeeded"


def test_pipeline_all_attempted_steps_failing_is_failed_outcome(monkeypatch):
    monkeypatch.setattr(
        actions, "do_refresh_and_prune_candidates",
        lambda safe=True, progress_callback=None: (_ for _ in ()).throw(
            actions.ActionError("refresh down")),
    )
    monkeypatch.setattr(
        actions, "do_reconcile_trades",
        lambda: (_ for _ in ()).throw(actions.ActionError("reconcile down")),
    )

    result = actions.do_pipeline(safe=True)

    assert set(result["failed_steps"]) == {
        "refresh_and_prune_candidates", "reconcile_trades",
    }
    assert actions.pipeline_outcome(result) == "failed"


def test_do_reconcile_trades_converts_esierror_to_actionerror(monkeypatch):
    # Direct POST /trades/reconcile only catches ActionError at _wrap -
    # an ESI 401/timeout here used to be a raw 500 even though do_pipeline
    # already isolated the same ESIError one level up.
    monkeypatch.setattr(actions.TokenManager, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(actions, "list_shared_trading_characters",
                        lambda tm: [("buyer", 1, "B"), ("seller", 2, "S")])
    monkeypatch.setattr(actions.storage, "load_shortlist", lambda: [])
    monkeypatch.setattr(actions, "ESIClient", lambda *a, **k: object())
    monkeypatch.setattr(actions, "collect_trading_wallet_streams", lambda *a, **k: ([], {}))

    def boom(*a, **k):
        raise ESIError("401 unauthorized")
    monkeypatch.setattr(actions, "reconcile_realized_trades", boom)

    try:
        actions.do_reconcile_trades()
        assert False, "expected ActionError"
    except actions.ActionError as e:
        assert "ESI access failed" in str(e)
