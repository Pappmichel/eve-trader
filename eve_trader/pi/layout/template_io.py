"""EVE PI template JSON <-> Layout.

Format, checked against real in-game exports (PI_PLAN 1.2/1.4):
  {"CmdCtrLv": 5, "Cmt": "...", "Diam": 5820.0,
   "L": [{"D": 6, "Lv": 0, "S": 3}, ...],                        links, 1-based pin indices
   "P": [{"H": 0, "La": 1.109, "Lo": 1.538, "S": 9832, "T": 2474}, ...],   pins
   "Pln": 2016,                                                  planet *type* id
   "R": [{"P": [11, 8, 13], "Q": 40, "T": 2390}, ...]}           routes: pin path, qty/cycle, commodity
- pin S = product type id for factories, P0 type id for extractors (not a
  schematic id); H = head count; no Command Center pin.
- La/Lo/Diam must be floats ("3.0", never "3") or the game rejects the paste
  (P-44/P-45) - so templates are serialized here, never in the browser.
- Keys are alphabetical in real exports, which is exactly json.dumps(sort_keys)
  - unchanged templates round-trip byte for byte (P-14).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from typing import Any, Optional

MAX_TEMPLATE_BYTES = 256 * 1024
MAX_PINS = 300
MAX_LINKS = 600
MAX_ROUTES = 600
MAX_ROUTE_PATH = 50
MAX_COMMENT_CHARS = 60


class TemplateError(ValueError):
    """The text is not a usable EVE PI template (with a user-facing reason)."""


@dataclass(frozen=True)
class Pin:
    type_id: int                    # structure type id ("T")
    la: float                       # polar angle ("La")
    lo: float                       # longitude ("Lo")
    product: Optional[int] = None   # "S": product (factory) / P0 (extractor)
    heads: int = 0                  # "H"


@dataclass(frozen=True)
class Link:
    a: int                          # 1-based pin index ("S")
    b: int                          # 1-based pin index ("D")
    level: int = 0                  # "Lv"


@dataclass(frozen=True)
class Route:
    path: tuple[int, ...]           # 1-based pin indices ("P")
    quantity: float                 # per cycle ("Q")
    commodity: int                  # type id ("T")


@dataclass(frozen=True)
class Layout:
    cc_level: int
    comment: str
    diameter_km: float
    planet_type_id: int
    pins: tuple[Pin, ...]
    links: tuple[Link, ...] = ()
    routes: tuple[Route, ...] = ()
    extra: tuple[tuple[str, Any], ...] = field(default=())   # unknown top-level keys, kept for round-trip

    def with_comment(self, comment: str) -> "Layout":
        return replace(self, comment=comment)


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TemplateError(f"{what} must be a number")
    f = float(v)
    if not math.isfinite(f):
        raise TemplateError(f"{what} must be finite")
    return f


def _int(v: Any, what: str) -> int:
    f = _num(v, what)
    if f != int(f):
        raise TemplateError(f"{what} must be a whole number")
    return int(f)


def parse(source: Any) -> Layout:
    """Text (JSON string) or an already-decoded dict -> Layout. Checks shape
    and size limits (P-57) before touching anything deeper."""
    if isinstance(source, (bytes, bytearray)):
        source = bytes(source).decode("utf-8-sig", errors="strict")
    if isinstance(source, str):
        if len(source.encode("utf-8")) > MAX_TEMPLATE_BYTES:
            raise TemplateError("Template is too large (max 256 KB)")
        try:
            data = json.loads(source.lstrip("﻿"))
        except json.JSONDecodeError as e:
            raise TemplateError(f"Not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})") from e
    else:
        data = source
    if not isinstance(data, dict):
        raise TemplateError("A template is a JSON object")
    for key in ("P", "Pln"):
        if key not in data:
            raise TemplateError(f"Missing \"{key}\" - this does not look like an EVE PI template")
    pins_raw, links_raw, routes_raw = data.get("P"), data.get("L") or [], data.get("R") or []
    if not isinstance(pins_raw, list) or not isinstance(links_raw, list) or not isinstance(routes_raw, list):
        raise TemplateError("\"P\", \"L\" and \"R\" must be lists")
    if len(pins_raw) > MAX_PINS or len(links_raw) > MAX_LINKS or len(routes_raw) > MAX_ROUTES:
        raise TemplateError(f"Too many structures/links/routes (max {MAX_PINS}/{MAX_LINKS}/{MAX_ROUTES})")
    pins = []
    for i, p in enumerate(pins_raw, start=1):
        if not isinstance(p, dict):
            raise TemplateError(f"Pin {i} is not an object")
        product = p.get("S")
        pins.append(Pin(
            type_id=_int(p.get("T"), f"Pin {i} T"),
            la=_num(p.get("La", 0.0), f"Pin {i} La"),
            lo=_num(p.get("Lo", 0.0), f"Pin {i} Lo"),
            product=None if product is None else _int(product, f"Pin {i} S"),
            heads=_int(p.get("H", 0) or 0, f"Pin {i} H"),
        ))
    n = len(pins)
    links = []
    for i, lk in enumerate(links_raw, start=1):
        if not isinstance(lk, dict):
            raise TemplateError(f"Link {i} is not an object")
        a, b = _int(lk.get("S"), f"Link {i} S"), _int(lk.get("D"), f"Link {i} D")
        if not (1 <= a <= n and 1 <= b <= n) or a == b:
            raise TemplateError(f"Link {i} points at a pin that does not exist")
        links.append(Link(a, b, _int(lk.get("Lv", 0) or 0, f"Link {i} Lv")))
    routes = []
    for i, r in enumerate(routes_raw, start=1):
        if not isinstance(r, dict):
            raise TemplateError(f"Route {i} is not an object")
        path = r.get("P") or []
        if not isinstance(path, list) or len(path) < 2 or len(path) > MAX_ROUTE_PATH:
            raise TemplateError(f"Route {i} needs a path of 2-{MAX_ROUTE_PATH} pins")
        idx = tuple(_int(x, f"Route {i} path") for x in path)
        if any(not (1 <= x <= n) for x in idx):
            raise TemplateError(f"Route {i} passes a pin that does not exist")
        routes.append(Route(idx, _num(r.get("Q", 0), f"Route {i} Q"), _int(r.get("T"), f"Route {i} T")))
    known = {"CmdCtrLv", "Cmt", "Diam", "L", "P", "Pln", "R"}
    extra = tuple(sorted((k, v) for k, v in data.items() if k not in known))
    return Layout(
        cc_level=_int(data.get("CmdCtrLv", 0) or 0, "CmdCtrLv"),
        comment=str(data.get("Cmt") or ""),
        diameter_km=_num(data.get("Diam", 0.0) or 0.0, "Diam"),
        planet_type_id=_int(data.get("Pln"), "Pln"),
        pins=tuple(pins), links=tuple(links), routes=tuple(routes), extra=extra,
    )


def _q(v: float):
    """Route quantities are integers in real exports; keep a fraction only
    if one was given."""
    return int(v) if float(v).is_integer() else float(v)


def to_dict(layout: Layout) -> dict:
    d: dict[str, Any] = dict(layout.extra)
    d.update({
        "CmdCtrLv": int(layout.cc_level),
        "Cmt": layout.comment,
        "Diam": float(layout.diameter_km),
        "L": [{"D": lk.b, "Lv": int(lk.level), "S": lk.a} for lk in layout.links],
        "P": [{"H": int(p.heads), "La": float(p.la), "Lo": float(p.lo), "S": p.product, "T": int(p.type_id)}
              for p in layout.pins],
        "Pln": int(layout.planet_type_id),
        "R": [{"P": list(r.path), "Q": _q(r.quantity), "T": int(r.commodity)} for r in layout.routes],
    })
    return d


def to_json(layout: Layout, pretty: bool = False) -> str:
    """One compact line for the in-game clipboard (default), or indented for
    a file. ensure_ascii keeps the comment safe for any clipboard."""
    if pretty:
        return json.dumps(to_dict(layout), sort_keys=True, indent=2, ensure_ascii=True)
    return json.dumps(to_dict(layout), sort_keys=True, ensure_ascii=True)


def safe_comment(text: str) -> str:
    """ASCII-only, at most 60 chars (the in-game field showed no limit, V-4;
    this is our own conservative cap, P-46)."""
    cleaned = "".join(ch if 32 <= ord(ch) < 127 else "-" for ch in (text or ""))
    return cleaned[:MAX_COMMENT_CHARS]
