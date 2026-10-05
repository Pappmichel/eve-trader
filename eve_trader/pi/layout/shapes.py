"""Layout shapes (docs/PI_PLAN.md 6A.2, phase 5c): other cell providers for
the generator. EVE accepts free positions as long as spacing, budget and
route rules hold (PI_PLAN 1.4), and links are paid by length, not by shape -
so a shape costs little as long as most links stay one cell long.

A provider returns `n` hex cells, the core hub's cell first. The generator
validates the result like any other layout; a shape that breaks a rule
(routes over 7 structures, budget) is refused and the caller falls back to
the standard layout with the reason (Eve-PI: "never applied half-way").
"""
from __future__ import annotations

import math
from typing import Callable

from .generate import Cell, CellProvider, GenerateError, cell_xy

_POOL_RING = 12
_HEX_DIRS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))


def _pool() -> list[Cell]:
    cells = []
    for q in range(-_POOL_RING, _POOL_RING + 1):
        for r in range(-_POOL_RING, _POOL_RING + 1):
            c = Cell(q, r)
            if c.ring <= _POOL_RING:
                cells.append(c)
    return cells


def _angle(c: Cell) -> float:
    x, y = cell_xy(c)
    return math.atan2(y, x) % (2 * math.pi)


def _by_key(key: Callable[[Cell], tuple]) -> CellProvider:
    def provider(n: int) -> list[Cell]:
        cells = sorted(_pool(), key=lambda c: key(c) + (round(_angle(c), 9),))
        if n > len(cells):
            raise GenerateError("Too many structures for this shape")
        out = cells[:n]
        if out and out[0] != Cell(0, 0):
            out.remove(Cell(0, 0)) if Cell(0, 0) in out else out.pop()
            out.insert(0, Cell(0, 0))
        return out
    return provider


def _on_axis(c: Cell, dirs) -> bool:
    for dq, dr in dirs:
        k = max(abs(c.q), abs(c.r), abs(c.q + c.r))
        if k and (c.q, c.r) == (dq * k, dr * k):
            return True
    return False


def _star_key(c: Cell) -> tuple:
    return (0 if c.ring == 0 or _on_axis(c, _HEX_DIRS) else 1, c.ring)


def _plus_key(c: Cell) -> tuple:
    # two perpendicular-ish axes: the q axis and the "vertical" zig-zag (x == 0 column)
    x, y = cell_xy(c)
    on = (c.r == 0) or abs(x) < 0.51
    return (0 if on else 1, c.ring)


def _grid_key(c: Cell) -> tuple:
    x, y = cell_xy(c)
    return (max(abs(x), abs(y) / (math.sqrt(3) / 2)),)


def _diamond_key(c: Cell) -> tuple:
    x, y = cell_xy(c)
    return (abs(x) + abs(y) / (math.sqrt(3) / 2),)


def _spiral_key(c: Cell) -> tuple:
    # distance grows with angle: one continuous arm around the core
    if c.ring == 0:
        return (0.0,)
    x, y = cell_xy(c)
    r = math.hypot(x, y)
    turns = r / 2.2
    phase = (_angle(c) / (2 * math.pi) - turns) % 1.0
    return (round(phase * 6) / 6 + r * 0.01, r)


def _ring_key(c: Cell) -> tuple:
    # the core, then a hollow ring at distance 3, then the next ring outward
    return (0 if c.ring == 0 else (1 if c.ring == 3 else (2 if c.ring == 4 else 3 + c.ring)), c.ring)


SHAPES: dict[str, CellProvider] = {
    "star": _by_key(_star_key),
    "plus": _by_key(_plus_key),
    "grid": _by_key(_grid_key),
    "diamond": _by_key(_diamond_key),
    "spiral": _by_key(_spiral_key),
    "ring": _by_key(_ring_key),
}


def provider(name: str) -> CellProvider:
    return SHAPES[name]
