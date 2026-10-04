"""B4 (docs/FRONTEND_PLAN.md): the shortlist refresh is the only writer of
stored history for items already on the shortlist, so it must save both the
buy hub's and the reference region's points."""
from eve_trader import actions, storage
from eve_trader.config import TradingConfig
from eve_trader.goonmetrics_client import GoonmetricsClient, HistoryPoint
from eve_trader.models import ShortlistItem

from .test_shortlist_pruning import _cleanup_refresh_mocks


def _point(region_id, type_id):
    return HistoryPoint(region_id=region_id, type_id=type_id, date="2026-10-01", min_price=1.0,
                        max_price=2.0, avg_price=1.5, movement=10.0, num_orders=3)


def test_refresh_saves_hub_and_reference_history(monkeypatch):
    cfg = TradingConfig(structure_id=1000, jita_region_id=10000043, reference_region_id=10000009)
    items = [ShortlistItem(item="A", item_id=1, category="X", volume_m3=1.0, meta_level=5, refreshed_at=None)]
    _cleanup_refresh_mocks(monkeypatch, items, lambda self, region_id, type_ids, max_workers=10: {})
    monkeypatch.setattr(GoonmetricsClient, "price_history_chunked",
                        lambda self, region_id, type_ids, **_kw: [_point(region_id, t) for t in type_ids])
    saved: list[tuple[int, int]] = []
    monkeypatch.setattr(storage, "save_goonmetrics_history",
                        lambda points: saved.extend((p.region_id, p.type_id) for p in points))

    actions.do_refresh_shortlist(cfg)

    assert sorted(saved) == [(10000009, 1), (10000043, 1)]


def test_hub_history_failure_does_not_lose_reference_history(monkeypatch):
    cfg = TradingConfig(structure_id=1000)
    items = [ShortlistItem(item="A", item_id=1, category="X", volume_m3=1.0, meta_level=5, refreshed_at=None)]
    _cleanup_refresh_mocks(monkeypatch, items, lambda self, region_id, type_ids, max_workers=10: {})

    def history(self, region_id, type_ids, **_kw):
        if region_id == cfg.jita_region_id:
            raise RuntimeError("goonmetrics down")
        return [_point(region_id, t) for t in type_ids]

    monkeypatch.setattr(GoonmetricsClient, "price_history_chunked", history)
    saved: list[int] = []
    monkeypatch.setattr(storage, "save_goonmetrics_history",
                        lambda points: saved.extend(p.region_id for p in points))

    result = actions.do_refresh_shortlist(cfg)

    assert saved == [cfg.reference_region_id]
    assert result["cleanup_items_skipped"] == 0
