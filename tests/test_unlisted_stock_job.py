"""The Production unlisted-stock check as a pipeline_runner background job:
start/status shape, one tenant slot (409), JSON-able result, status scoping by
job name, early validations staying immediate, and the read scope still
applying inside the worker."""
import threading
import time

import pytest

from eve_trader import pipeline_runner, storage
from eve_trader.actions import ConflictError
from eve_trader.auth import TokenManager
from eve_trader.esi_client import ESIClient
from eve_trader.production import actions
from eve_trader.production.config import ProductionConfig
from eve_trader.production.models import UnlistedStockRow

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant  # noqa: F401

pytestmark = pg_helpers.postgres_required()

HOME = 1000000000001


@pytest.fixture(autouse=True)
def _clean():
    pg_helpers.wipe_tables("pipeline_runs")
    yield
    pg_helpers.wipe_tables("pipeline_runs")


def _wait_finished(timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = actions.do_unlisted_stock_status()
        if st["status"] not in ("running", "idle"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _allow_start(monkeypatch):
    monkeypatch.setattr(actions, "_unlisted_preconditions", lambda cfg: ([], [("producer:1", 1, "C")]))


def test_start_returns_run_id_and_result_rows_are_json_able(monkeypatch, tenant):
    _allow_start(monkeypatch)
    row = UnlistedStockRow(type_id=205, type_name="Nova Cruise Missile", stock_quantity=50.0,
                           sell_volume=12.0, margin=0.25, doctrine_shortfall=None,
                           doctrine_names=["Ishtars"], production_needed=7.0)
    monkeypatch.setattr(actions, "do_unlisted_stock", lambda cfg=None: {"rows": [row]})

    started = actions.do_start_unlisted_stock()
    assert started["status"] == "running" and started["run_id"]
    assert started["job_name"] == pipeline_runner.JOB_UNLISTED_STOCK

    st = _wait_finished()
    assert st["status"] == "succeeded"
    assert st["result"] == {"rows": [{
        "type_id": 205, "type_name": "Nova Cruise Missile", "stock_quantity": 50.0,
        "sell_volume": 12.0, "margin": 0.25, "doctrine_shortfall": None,
        "doctrine_names": ["Ishtars"], "production_needed": 7.0,
    }]}


def test_second_start_while_running_is_a_conflict(monkeypatch, tenant):
    _allow_start(monkeypatch)
    release = threading.Event()
    monkeypatch.setattr(actions, "do_unlisted_stock", lambda cfg=None: release.wait(20) and {"rows": []})
    actions.do_start_unlisted_stock()
    try:
        with pytest.raises(ConflictError, match="already running"):
            actions.do_start_unlisted_stock()
        # another tool job is blocked by the same tenant slot
        with pytest.raises(ConflictError):
            pipeline_runner.start_rebuild_realized()
    finally:
        release.set()
    assert _wait_finished()["status"] == "succeeded"


def test_discover_status_never_shows_the_unlisted_job(monkeypatch, tenant):
    _allow_start(monkeypatch)
    monkeypatch.setattr(actions, "do_unlisted_stock", lambda cfg=None: {"rows": []})
    actions.do_start_unlisted_stock()
    _wait_finished()
    assert actions.do_discover_build_candidates_status()["status"] == "idle"
    assert actions.do_unlisted_stock_status()["job_name"] == pipeline_runner.JOB_UNLISTED_STOCK


def test_unlisted_status_never_shows_a_discover_run(tenant):
    run_id = storage.start_pipeline_run(pipeline_runner.JOB_DISCOVER_BUILD_CANDIDATES, tool="production")
    try:
        assert actions.do_unlisted_stock_status()["status"] == "idle"
        assert actions.do_discover_build_candidates_status()["run_id"] == run_id
    finally:
        storage.finish_pipeline_run(run_id, "succeeded", result={})


def test_early_validation_is_immediate_and_starts_no_run(monkeypatch, tenant):
    with pytest.raises(actions.ActionError, match="structure/location"):
        actions.do_start_unlisted_stock(ProductionConfig(home_location_id=None))
    monkeypatch.setattr(actions.dynamic_targets, "effective_stock_targets", lambda cfg: [])
    with pytest.raises(actions.ActionError, match="No stock targets"):
        actions.do_start_unlisted_stock(ProductionConfig(home_location_id=HOME))
    monkeypatch.setattr(actions.dynamic_targets, "effective_stock_targets", lambda cfg: [(1, "Item", 0, 50, None)])
    monkeypatch.setattr(actions.esi_sync, "list_shared_producer_characters", lambda: [])
    with pytest.raises(actions.ActionError, match="No Production character"):
        actions.do_start_unlisted_stock(ProductionConfig(home_location_id=HOME))
    assert actions.do_unlisted_stock_status()["status"] == "idle"


def test_failure_in_worker_lands_as_failed_with_text(monkeypatch, tenant):
    _allow_start(monkeypatch)

    def boom(cfg=None):
        raise actions.ActionError("ESI exploded")
    monkeypatch.setattr(actions, "do_unlisted_stock", boom)
    actions.do_start_unlisted_stock()
    st = _wait_finished()
    assert st["status"] == "failed" and "ESI exploded" in st["error"]


def test_read_scope_applies_inside_the_worker(monkeypatch, tenant):
    """A character sharing only Market Orders must not contribute asset stock
    when the check runs as a background job either."""
    class _FakeCtx:
        def __init__(self, cfg, extra_type_ids=()):
            pass
    monkeypatch.setattr(actions, "_PlanContext", _FakeCtx)
    monkeypatch.setattr(actions, "_item_margin_detail_with_context", lambda *a, **k: {"margin_home": None})
    monkeypatch.setattr(storage, "load_stock_targets", lambda: [(1, "Item", 0, 50, None)])
    monkeypatch.setattr(actions.esi_sync, "list_shared_producer_characters",
                        lambda: [("producer:1", 1, "Orders"), ("producer:2", 2, "Assets")])
    monkeypatch.setattr(actions, "_unlisted_read_scope", lambda: {
        "asset_chars": {2}, "order_chars": {1}, "asset_corps": set(), "order_corps": {500}})
    monkeypatch.setattr(actions, "production_needs_by_type", lambda: {})
    monkeypatch.setattr(TokenManager, "__init__", lambda self, *a, **kw: None)
    asset_reads = []

    def char_assets(self, character_id, auth_role):
        asset_reads.append(character_id)
        return [{"item_id": 10 + character_id, "type_id": 1, "quantity": 100,
                 "location_id": HOME, "location_flag": "Hangar"}]

    monkeypatch.setattr(ESIClient, "character_assets", char_assets)
    monkeypatch.setattr(ESIClient, "character_orders", lambda self, character_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "character_public_info", lambda self, character_id: {"corporation_id": 500})
    monkeypatch.setattr(ESIClient, "corporation_assets", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "corporation_orders", lambda self, corporation_id, auth_role: [])
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", lambda self, *a, **k: {})
    monkeypatch.setattr(actions.dynamic_targets, "effective_stock_targets", lambda cfg: [(1, "Item", 0, 50, None)])
    # The worker resolves its own tenant config (no home location); hand the
    # real do_unlisted_stock one explicitly, everything else is the real path.
    real = actions.do_unlisted_stock
    monkeypatch.setattr(actions, "do_unlisted_stock",
                        lambda cfg=None: real(ProductionConfig(home_location_id=HOME)))

    actions.do_start_unlisted_stock(ProductionConfig(home_location_id=HOME))
    st = _wait_finished()
    assert st["status"] == "succeeded", st
    assert asset_reads == [2]
    assert [r["stock_quantity"] for r in st["result"]["rows"]] == [100.0]
