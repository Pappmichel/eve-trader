from eve_trader import actions
from eve_trader.esi_client import ESIError


def test_pipeline_step_isolation_survives_esi_error_in_rebuild(monkeypatch):
    # The pipeline's last step used to call do_reconcile_trades and only
    # caught ActionError. An ESI failure during the shared rebuild is
    # isolated the same way as the other steps.
    from eve_trader.production import actions as production_actions
    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", lambda safe=True, progress_callback=None: {"ok": True})

    def boom(reset_opening=False):
        raise ESIError("420 rate limited")
    monkeypatch.setattr(production_actions, "do_rebuild_realized", boom)

    result = actions.do_pipeline(safe=True)

    assert result["refresh_and_prune_candidates"] == {"ok": True}
    assert "error" in result["rebuild_realized"]
    assert result["failed_steps"]["rebuild_realized"] == "420 rate limited"
    assert actions.pipeline_outcome(result) == "degraded"


def test_pipeline_step_isolation_survives_action_error_in_rebuild(monkeypatch):
    from eve_trader.production import actions as production_actions
    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", lambda safe=True, progress_callback=None: {"ok": True})

    def boom(reset_opening=False):
        raise actions.ActionError("realized profit rebuild failed")
    monkeypatch.setattr(production_actions, "do_rebuild_realized", boom)

    result = actions.do_pipeline(safe=True)

    assert result["refresh_and_prune_candidates"] == {"ok": True}
    assert "error" in result["rebuild_realized"]
    assert actions.pipeline_outcome(result) == "degraded"


def test_pipeline_refresh_failure_still_rebuilds_and_reports_failed_steps(monkeypatch):
    from eve_trader.production import actions as production_actions
    ran = []

    def boom(safe=True, progress_callback=None):
        raise actions.ActionError("shortlist ESI failed")

    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", boom)
    monkeypatch.setattr(
        production_actions, "do_rebuild_realized",
        lambda reset_opening=False: ran.append("rebuild") or {"realized_results": 0},
    )

    result = actions.do_pipeline(safe=True)

    assert ran == ["rebuild"]
    assert result["failed_steps"]["refresh_and_prune_candidates"] == "shortlist ESI failed"
    assert result["rebuild_realized"] == {"realized_results": 0}
    assert actions.pipeline_outcome(result) == "degraded"


def test_pipeline_full_success_has_no_failed_steps(monkeypatch):
    from eve_trader.production import actions as production_actions
    monkeypatch.setattr(
        actions, "do_refresh_and_prune_candidates",
        lambda safe=True, progress_callback=None: {"ok": True},
    )
    monkeypatch.setattr(production_actions, "do_rebuild_realized", lambda reset_opening=False: {"ok": True})

    result = actions.do_pipeline(safe=True)

    assert "failed_steps" not in result
    assert result["refresh_and_prune_candidates"] == {"ok": True}
    assert result["rebuild_realized"] == {"ok": True}
    assert actions.pipeline_outcome(result) == "succeeded"


def test_pipeline_all_attempted_steps_failing_is_failed_outcome(monkeypatch):
    from eve_trader.production import actions as production_actions
    monkeypatch.setattr(
        actions, "do_refresh_and_prune_candidates",
        lambda safe=True, progress_callback=None: (_ for _ in ()).throw(
            actions.ActionError("refresh down")),
    )
    monkeypatch.setattr(
        production_actions, "do_rebuild_realized",
        lambda reset_opening=False: (_ for _ in ()).throw(actions.ActionError("rebuild down")),
    )

    result = actions.do_pipeline(safe=True)

    assert set(result["failed_steps"]) == {
        "refresh_and_prune_candidates", "rebuild_realized",
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
    monkeypatch.setattr(actions, "collect_trading_wallet_streams", lambda *a, **k: ([], {}, 1))

    def boom(*a, **k):
        raise ESIError("401 unauthorized")
    monkeypatch.setattr(actions, "reconcile_realized_trades", boom)

    try:
        actions.do_reconcile_trades()
        assert False, "expected ActionError"
    except actions.ActionError as e:
        assert "ESI access failed" in str(e)


def test_do_reconcile_trades_refuses_to_wipe_history_when_no_wallet_is_readable(monkeypatch):
    """Confirmed real, destructive bug 2026-09-21: every character can pass
    the "shared with Trading" guard on Market Orders or Assets sharing
    alone, while collect_trading_wallet_streams gates each owner on *Wallet*
    sharing - so it returned a silent ([], {}), reconcile matched nothing,
    and storage.save_realized_trades([]) (DELETE-then-insert) wiped the
    entire realized-trade history with `matched_trades: 0` looking like
    "nothing to match"."""
    saved = []
    monkeypatch.setattr(actions.TokenManager, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(actions, "list_shared_trading_characters",
                        lambda tm: [("buyer:1", 1, "B")])
    monkeypatch.setattr(actions.storage, "load_shortlist", lambda: [])
    monkeypatch.setattr(actions, "ESIClient", lambda *a, **k: object())
    # 0 readable owners - nothing shares Wallet, so the empty result is an
    # access problem, not the truth about this tenant's trades.
    monkeypatch.setattr(actions, "collect_trading_wallet_streams", lambda *a, **k: ([], {}, 0))
    monkeypatch.setattr(actions.storage, "save_realized_trades",
                        lambda trades, run_ts: saved.append(trades))

    try:
        actions.do_reconcile_trades()
        assert False, "expected ActionError"
    except actions.ActionError as e:
        assert "shares Wallet with Trading" in str(e)
    assert saved == [], "must not touch realized_trades when no wallet was readable"


def test_do_reconcile_trades_still_saves_an_empty_run_when_a_wallet_was_readable(monkeypatch):
    """The other side of the guard above: a readable wallet that genuinely
    has no matching trades in the window must still save [] (the wholesale
    replace is correct there - the emptiness is the real answer)."""
    saved = []
    monkeypatch.setattr(actions.TokenManager, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(actions, "list_shared_trading_characters",
                        lambda tm: [("buyer:1", 1, "B")])
    monkeypatch.setattr(actions.storage, "load_shortlist", lambda: [])
    monkeypatch.setattr(actions, "ESIClient", lambda *a, **k: object())
    monkeypatch.setattr(actions, "collect_trading_wallet_streams", lambda *a, **k: ([], {}, 1))
    monkeypatch.setattr(actions, "reconcile_realized_trades", lambda *a, **k: [])
    monkeypatch.setattr(actions.storage, "save_realized_trades",
                        lambda trades, run_ts: saved.append(trades))

    result = actions.do_reconcile_trades()

    assert result["matched_trades"] == 0
    assert saved == [[]]
