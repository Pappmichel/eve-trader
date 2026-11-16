"""Request length limits: shared Annotated types on every request model, the
1 MiB body limit (pure ASGI middleware), and bounded free-form JSON bodies.
Response models stay unbounded. No Postgres: handlers that would reach the
database are monkeypatched, and validation fails before them."""
from __future__ import annotations

import importlib
import pkgutil
import typing

import pytest
from annotated_types import Len, MaxLen
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter, ValidationError

import eve_trader.api.routers as routers_pkg
from eve_trader import scheduler
from eve_trader.api import schemas
from eve_trader.api.app import create_app
from eve_trader.api.body_limit import MAX_BODY_BYTES
from eve_trader.api.partial import bound_annotation, partial_model
from eve_trader.api.routers import doctrine as doctrine_router
from eve_trader.api.routers import production as production_router
from eve_trader.api.routers import trading as trading_router

client = TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_scheduler(monkeypatch):
    monkeypatch.setattr(scheduler, "start", lambda: None)


# ---------------------------------------------------------------- shared types
@pytest.mark.parametrize("tp, limit", [
    (schemas.NameText, 100), (schemas.ShortText, 200), (schemas.LongText, 10_000), (schemas.PasteText, 500_000),
])
def test_text_types_reject_one_character_too_many(tp, limit):
    adapter = TypeAdapter(tp)
    assert adapter.validate_python("x" * limit) == "x" * limit
    with pytest.raises(ValidationError):
        adapter.validate_python("x" * (limit + 1))


def test_list_types_reject_one_entry_too_many():
    ids = TypeAdapter(schemas.IdList)
    assert len(ids.validate_python(list(range(50_000)))) == 50_000
    with pytest.raises(ValidationError):
        ids.validate_python(list(range(50_001)))
    texts = TypeAdapter(schemas.ShortTextList)
    assert len(texts.validate_python(["a"] * 1_000)) == 1_000
    with pytest.raises(ValidationError):
        texts.validate_python(["a"] * 1_001)
    with pytest.raises(ValidationError):
        texts.validate_python(["x" * 201])


def test_skill_levels_are_bounded():
    levels = TypeAdapter(schemas.SkillLevels)
    assert levels.validate_python({"a": 0, "b": 5}) == {"a": 0, "b": 5}
    with pytest.raises(ValidationError):
        levels.validate_python({"a": 6})
    with pytest.raises(ValidationError):
        levels.validate_python({f"k{i}": 1 for i in range(51)})


def test_bounded_json_size_depth_and_keys():
    template = TypeAdapter(schemas.BoundedTemplate)
    assert template.validate_python({"Pins": [{"S": 1}]}) == {"Pins": [{"S": 1}]}
    with pytest.raises(ValidationError):
        template.validate_python({"x": "a" * schemas.TEMPLATE_MAX_BYTES})
    deep: object = 1
    for _ in range(50):
        deep = {"a": deep}
    with pytest.raises(ValidationError):
        TypeAdapter(schemas.BoundedDesign).validate_python(deep)
    # 31 keys fail, 30 pass.
    query = TypeAdapter(schemas.BoundedViewQuery)
    assert query.validate_python({f"k{i}": 1 for i in range(30)})
    with pytest.raises(ValidationError):
        query.validate_python({f"k{i}": 1 for i in range(31)})
    with pytest.raises(ValidationError):
        query.validate_python({"q": "x" * (16 * 1024)})
    # A hostile nesting depth fails cleanly instead of recursing.
    hostile: object = []
    for _ in range(20_000):
        hostile = [hostile]
    with pytest.raises(ValidationError):
        template.validate_python(hostile)


# ----------------------------------------------- every request model is bounded
def _has_limit(meta):
    for m in meta:
        if isinstance(m, MaxLen) or (isinstance(m, Len) and m.max_length is not None):
            return True
        if getattr(m, "max_length", None) is not None or _has_limit(tuple(getattr(m, "metadata", ()) or ())):
            return True
    return False


def _unbounded(ann, path, out):
    origin, args = typing.get_origin(ann), typing.get_args(ann)
    if origin is typing.Annotated:
        meta = args[1:]
        if any(type(m).__name__ == "AfterValidator" for m in meta):
            return  # bounded_json: size and depth checked (key count via Field where it matters)
        if _has_limit(meta):
            if typing.get_origin(args[0]) in (list, set, dict, tuple):
                # the container is bounded; its elements must be too
                for sub in typing.get_args(args[0]):
                    _unbounded(sub, path + "[]", out)
            return
        return _unbounded(args[0], path, out)
    if ann is str:
        out.append(path + ": unbounded str")
    elif ann is typing.Any or ann in (dict, list):
        out.append(path + ": unbounded Any")
    elif origin in (list, set, frozenset, tuple, dict):
        out.append(path + ": unbounded " + origin.__name__)
    elif origin is typing.Union or (origin is not None and "UnionType" in str(origin)):
        for sub in args:
            _unbounded(sub, path, out)
    elif isinstance(ann, type) and issubclass(ann, BaseModel):
        for name, field in ann.model_fields.items():
            full = typing.Annotated[(field.annotation, *field.metadata)] if field.metadata else field.annotation
            _unbounded(full, f"{path}.{name}", out)


def _request_models():
    found = {}
    for mod_info in pkgutil.iter_modules(routers_pkg.__path__):
        module = importlib.import_module(f"{routers_pkg.__name__}.{mod_info.name}")
        for route in getattr(getattr(module, "router", None), "routes", []):
            if not isinstance(route, APIRoute):
                continue
            for param in route.dependant.body_params:
                info = param.field_info
                ann = typing.Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation
                found[f"{mod_info.name} {route.path} {param.name}"] = ann
    return found


def test_no_request_body_has_an_unbounded_string_list_dict_or_any():
    problems = []
    for where, ann in _request_models().items():
        out: list[str] = []
        _unbounded(ann, "", out)
        problems += [f"{where}{p}" for p in out]
    assert not problems, "\n".join(problems)


# ------------------------------------------------------ settings (partial) models
def test_partial_settings_models_keep_the_limits():
    update = trading_router.TradingSettingsUpdate
    assert update(excluded_path_prefixes=["a" * 200]).excluded_path_prefixes == ["a" * 200]
    with pytest.raises(ValidationError):
        update(excluded_path_prefixes=["a" * 201])
    with pytest.raises(ValidationError):
        update(excluded_path_prefixes=["a"] * 1_001)
    with pytest.raises(ValidationError):
        trading_router.TradingSettingsUpdate(buyer_character_name="a" * 201)
    with pytest.raises(ValidationError):
        production_router.ProductionSettingsUpdate(excluded_system_ids=list(range(50_001)))
    with pytest.raises(ValidationError):
        doctrine_router.DoctrineSettingsUpdate(stockpile_location_ids=list(range(50_001)))


def test_partial_model_carries_a_top_level_annotated_limit():
    class Base(BaseModel):
        ids: schemas.IdList = []
        name: typing.Annotated[str, schemas.StringConstraints(max_length=5)] = ""

    update = partial_model(Base, "Upd")
    with pytest.raises(ValidationError):
        update(ids=list(range(50_001)))
    with pytest.raises(ValidationError):
        update(name="toolong")
    assert update().model_dump(exclude_unset=True) == {}


def test_bound_annotation_leaves_existing_limits_and_bounds_the_rest():
    assert bound_annotation(int) is int
    with pytest.raises(ValidationError):
        TypeAdapter(bound_annotation(list[str])).validate_python(["x"] * 1_001)
    with pytest.raises(ValidationError):
        TypeAdapter(bound_annotation(typing.Optional[str])).validate_python("x" * 201)
    own = typing.Annotated[str, schemas.StringConstraints(max_length=5000)]
    assert bound_annotation(own) is own


def test_settings_save_with_a_too_long_value_is_422_over_http():
    response = client.post("/api/trading/settings", json={"excluded_path_prefixes": ["x" * 201]})
    assert response.status_code == 422
    response = client.post("/api/trading/settings", json={"buyer_character_name": "x" * 201})
    assert response.status_code == 422


def test_request_models_are_bounded_over_http():
    assert client.post("/api/portfolio/manual-prices", json={"item_name": "x" * 101, "price": 1}).status_code == 422
    assert client.post("/api/refining/reprocessing/quote", json={"paste": "x" * 500_001}).status_code in (413, 422)
    assert client.post("/api/refining/shortlist/activate", json={"item_ids": list(range(50_001))}).status_code == 422


def test_response_models_carry_no_length_limits():
    # A long stored value must still be readable.
    row = schemas.Candidate(item="x" * 5_000, type_id=1, volume_m3=1.0, category="c", market_group_path="p")
    assert len(row.item) == 5_000


# --------------------------------------------------------------- body size limit
def test_body_at_the_limit_passes_the_middleware(monkeypatch):
    seen = {}

    def fake_preview(text, location_id, mode):
        seen["n"] = len(text)
        return {"ok": True}

    monkeypatch.setattr(production_router.actions, "do_preview_asset_paste", fake_preview)
    text = "Tritanium\t100\n" * 25_000  # ~375 KB, above the 300 KB a real paste reaches
    response = client.post("/api/production/manual-stock/paste/preview", json={"text": text})
    assert response.status_code == 200, response.text
    assert seen["n"] == len(text)


def test_body_over_one_mib_is_413_with_content_length():
    body = b'{"paste": "' + b"a" * MAX_BODY_BYTES + b'"}'
    response = client.post("/api/refining/reprocessing/quote", content=body,
                           headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}
    assert response.headers.get("x-request-id")


def test_body_over_one_mib_is_413_without_content_length():
    def chunks():
        yield b'{"paste": "'
        for _ in range(MAX_BODY_BYTES // 65_536 + 2):
            yield b"a" * 65_536
        yield b'"}'

    response = client.post("/api/refining/reprocessing/quote", content=chunks(),
                           headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}
    assert response.headers.get("x-request-id")


def test_body_of_exactly_the_limit_is_not_refused_by_the_middleware():
    body = b" " * MAX_BODY_BYTES
    response = client.post("/api/refining/reprocessing/quote", content=body,
                           headers={"content-type": "application/json"})
    assert response.status_code != 413


def test_non_api_paths_are_not_limited():
    body = b"a" * (MAX_BODY_BYTES + 1)
    response = client.post("/not-api", content=body)
    assert response.status_code != 413


# --------------------------------------------------------------- free-form bodies
def test_template_over_256_kib_is_422():
    big = {"Pins": [], "Comment": "x" * (256 * 1024)}
    response = client.post("/api/pi/layouts/validate", json={"template": big})
    assert response.status_code == 422


def test_design_nested_fifty_deep_is_422():
    deep: object = 1
    for _ in range(50):
        deep = {"a": deep}
    response = client.post("/api/pi/planner", json={"chain": "p1", "product_type_id": 1, "design": deep})
    assert response.status_code == 422


def test_view_query_with_31_keys_is_422():
    response = client.post("/api/asset-manager/views", json={"name": "v", "query": {f"k{i}": 1 for i in range(31)}})
    assert response.status_code == 422


def test_pi_settings_with_101_keys_is_422():
    response = client.put("/api/pi/settings", json={f"k{i}": 1 for i in range(101)})
    assert response.status_code == 422


@pytest.mark.parametrize("opener, closer", [(b"[", b""), (b'{"a":', b"")])
def test_deeply_nested_json_is_never_a_500(opener, closer):
    count = (MAX_BODY_BYTES - 16) // len(opener)
    body = opener * count + closer
    for path, method in (("/api/pi/layouts/validate", "post"), ("/api/pi/plans", "post"),
                         ("/api/asset-manager/views", "post")):
        response = getattr(client, method)(path, content=body, headers={"content-type": "application/json"})
        assert response.status_code < 500, (path, response.status_code)
