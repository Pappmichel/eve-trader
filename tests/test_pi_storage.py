"""PI plan storage: round trip (incl. JSONB design) and tenant isolation."""
import pytest

from eve_trader import storage

from . import pg_helpers
from .pg_helpers import _apply_phase1_schema, tenant, tenant_pair  # noqa: F401

pytestmark = pg_helpers.postgres_required()

_DESIGN = {"chain": "P0-P1", "planet_type_id": 11, "product_type_id": 2389,
           "ecus": [[1, 4]], "factories": [[2389, 3]], "launchpads": 1, "storages": 0}


@pytest.fixture(autouse=True)
def _wipe():
    pg_helpers.wipe_tables("pi_plans")
    yield
    pg_helpers.wipe_tables("pi_plans")


def _plan(name="Plasma colony", **over):
    return {"name": name, "planet_id": None, "planet_type_id": 11, "radius_km": 4200.0,
            "character_id": None, "design": _DESIGN, "owner_tax_rate": 0.05,
            "freight_per_m3": None, "yield_override": None, "notes": "n", **over}


def test_round_trip(tenant):
    plan_id = storage.save_pi_plan(_plan())
    got = storage.get_pi_plan(plan_id)
    assert got["name"] == "Plasma colony" and got["design"] == _DESIGN
    assert got["owner_tax_rate"] == 0.05 and got["radius_km"] == 4200.0 and got["created_at"]
    assert [p["plan_id"] for p in storage.list_pi_plans()] == [plan_id]

    new_design = {**_DESIGN, "launchpads": 2}
    assert storage.save_pi_plan(_plan(name="Renamed", design=new_design), plan_id) == plan_id
    got = storage.get_pi_plan(plan_id)
    assert got["name"] == "Renamed" and got["design"]["launchpads"] == 2

    assert storage.delete_pi_plan(plan_id) is True
    assert storage.get_pi_plan(plan_id) is None
    assert storage.delete_pi_plan(plan_id) is False


def test_update_unknown_plan_raises(tenant):
    with pytest.raises(KeyError):
        storage.save_pi_plan(_plan(), 999999)


def test_plans_are_tenant_isolated(tenant_pair):
    a, b = tenant_pair
    with storage.tenant_context(a):
        a_id = storage.save_pi_plan(_plan("A"))
    with storage.tenant_context(b):
        assert storage.list_pi_plans() == []
        assert storage.get_pi_plan(a_id) is None
        assert storage.delete_pi_plan(a_id) is False
        with pytest.raises(KeyError):
            storage.save_pi_plan(_plan("hijack"), a_id)
    with storage.tenant_context(a):
        assert storage.get_pi_plan(a_id)["name"] == "A"


# ------------------------------------------------------------ templates
_TEMPLATE = {"CmdCtrLv": 5, "Cmt": "Factory", "Diam": 3.0, "L": [{"D": 2, "Lv": 0, "S": 1}],
             "P": [{"H": 0, "La": 1.5708, "Lo": 0.0, "S": None, "T": 2544}], "Pln": 2016,
             "R": [{"P": [1, 2], "Q": 40, "T": 3645}]}


@pytest.fixture
def _wipe_templates():
    pg_helpers.wipe_tables("pi_templates")
    yield
    pg_helpers.wipe_tables("pi_templates")


def _save_tpl(name="T", template=None, tid=None, source="paste"):
    return storage.save_pi_template(name, "cmt", 2016, 5, 3.0, template or _TEMPLATE, source, tid)


def test_template_round_trip(tenant, _wipe_templates):
    tid = _save_tpl("Zeta")
    got = storage.get_pi_template(tid)
    assert got["template"] == _TEMPLATE
    assert isinstance(got["template"]["Diam"], float) and got["template"]["P"][0]["La"] == 1.5708
    assert got["template"]["P"][0]["Lo"] == 0.0 and got["template"]["P"][0]["S"] is None
    assert got["name"] == "Zeta" and got["planet_type_id"] == 2016 and got["cc_level"] == 5
    assert got["diameter_km"] == 3.0 and got["source"] == "paste" and got["created_at"]

    other = _save_tpl("Alpha")
    listed = storage.list_pi_templates()
    assert [r["name"] for r in listed] == ["Alpha", "Zeta"] and all("template" not in r for r in listed)

    new_tpl = {**_TEMPLATE, "Cmt": "changed"}
    assert _save_tpl("Renamed", new_tpl, tid, "generated") == tid
    got = storage.get_pi_template(tid)
    assert got["name"] == "Renamed" and got["template"]["Cmt"] == "changed" and got["source"] == "generated"

    assert storage.delete_pi_template(tid) is True
    assert storage.get_pi_template(tid) is None
    assert storage.delete_pi_template(tid) is False
    assert [r["template_id"] for r in storage.list_pi_templates()] == [other]


def test_update_unknown_template_raises(tenant, _wipe_templates):
    with pytest.raises(KeyError):
        _save_tpl(tid=999999)


def test_templates_are_tenant_isolated(tenant_pair, _wipe_templates):
    a, b = tenant_pair
    with storage.tenant_context(a):
        a_id = _save_tpl("A")
    with storage.tenant_context(b):
        assert storage.list_pi_templates() == []
        assert storage.get_pi_template(a_id) is None
        assert storage.delete_pi_template(a_id) is False
        with pytest.raises(KeyError):
            _save_tpl("hijack", tid=a_id)
    with storage.tenant_context(a):
        assert storage.get_pi_template(a_id)["name"] == "A"
