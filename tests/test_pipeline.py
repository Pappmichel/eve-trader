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

