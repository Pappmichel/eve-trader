"""The Margin page builds production.models.ShipMarginRow from the engine's
row dicts and returns it through the API schema of the same name. A field
the engine and the schema know but the dataclass lacks makes every request a
500, and the router tests mock this path, so guard the shapes directly."""
import dataclasses

from eve_trader.api import schemas
from eve_trader.production import actions as production_actions
from eve_trader.production import models


def test_ship_margin_dataclass_matches_api_schema():
    dataclass_fields = {f.name for f in dataclasses.fields(models.ShipMarginRow)}
    assert dataclass_fields == set(schemas.ShipMarginRow.model_fields)


def test_ship_margins_accepts_invention_cost(monkeypatch):
    row = {
        "type_id": 1, "type_name": "Hull", "activity": "Tech II",
        "home_price": 10.0, "jita_price": 9.0, "build_cost": 5.0,
        "invention_cost_per_unit": 1.5,
        "margin_home": 0.5, "margin_jita": 0.4, "meta_level": 5,
    }
    monkeypatch.setattr(production_actions.storage, "sde_row_counts", lambda: {"sde_types": 1})
    monkeypatch.setattr(production_actions, "discover_ship_margins", lambda cfg: [dict(row)])

    result = production_actions.do_get_ship_margins()

    assert result["rows"][0].invention_cost_per_unit == 1.5
