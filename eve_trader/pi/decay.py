"""CCP's extractor yield formula (developers.eveonline.com/docs/guides/pi/).

Verified exactly in game on 2026-10-04 (PI_TECHNICAL_DESIGN 3.5, V-2): a
50 h program with 1 h cycles and base q = 5903 gives cycle 1 = 23,058,
cycle 50 = 10,654 and a total of 682,147 - all three to the unit, with
integer truncation per cycle. `q` is the base per 15-minute bar, not per
cycle (P-32); a cycle yields about (cycle_seconds / 900) x q.
"""
from __future__ import annotations

import math
from functools import lru_cache

from . import constants as C

DEFAULT_DECAY_FACTOR = 0.012
DEFAULT_NOISE_FACTOR = 0.8


def cycle_seconds_for_program(program_hours: float) -> int:
    """15 min below 25 h, doubling at 25/50/100/200 h, 4 h up to 14 days
    (EVE Uni, confirmed in game - V-1)."""
    for threshold, seconds in C.PROGRAM_CYCLE_STEPS:
        if program_hours >= threshold:
            return seconds
    return C.PROGRAM_CYCLE_STEPS[-1][1]


def cycle_outputs(
    qty_per_cycle: int, cycle_seconds: int, cycles: int, noise: bool = True,
    decay_factor: float = DEFAULT_DECAY_FACTOR, noise_factor: float = DEFAULT_NOISE_FACTOR,
) -> list[int]:
    """Units extracted per cycle, exactly as the game computes them."""
    bar_width = cycle_seconds / 900.0
    phase = qty_per_cycle ** 0.7
    out = []
    for i in range(cycles):
        t = (i + 0.5) * bar_width
        decay = qty_per_cycle / (1.0 + t * decay_factor)
        if noise:
            s = (math.cos(phase + t * (1.0 / 12.0)) + math.cos(phase / 2.0 + t * 0.2) + math.cos(t * 0.5)) / 3.0
            height = decay * (1.0 + noise_factor * max(s, 0.0))
        else:
            height = decay
        out.append(int(bar_width * height))
    return out


def program_total(qty_per_cycle: int, cycle_seconds: int, program_seconds: float, noise: bool = True) -> int:
    cycles = int(program_seconds // cycle_seconds)
    return sum(cycle_outputs(qty_per_cycle, cycle_seconds, cycles, noise=noise))


@lru_cache(maxsize=512)
def _noise_free_average(program_hours: float, decay_factor: float) -> float:
    """Average output per hour per unit of q, noise-free, without integer
    truncation (a smooth curve for ratios). Linear in q, so q = 1."""
    cycle = cycle_seconds_for_program(program_hours)
    bar_width = cycle / 900.0
    cycles = max(1, int(program_hours * 3600 // cycle))
    total = 0.0
    for i in range(cycles):
        t = (i + 0.5) * bar_width
        total += bar_width / (1.0 + t * decay_factor)
    return total / (cycles * cycle / 3600.0)


def program_ratio(program_hours: float, reference_hours: float = C.REFERENCE_PROGRAM_HOURS,
                  decay_factor: float = DEFAULT_DECAY_FACTOR) -> float:
    """Average yield of a program of `program_hours` relative to one of
    `reference_hours`. Noise-free on purpose: noise only adds yield and
    roughly equally for both (P-64); for absolute calibration use the full
    formula instead."""
    program_hours = min(max(program_hours, 1.0), float(C.MAX_PROGRAM_HOURS))
    return _noise_free_average(program_hours, decay_factor) / _noise_free_average(reference_hours, decay_factor)


def ecu_route_quantity(heads: int, per_head_per_hour: float, cycle_seconds: int) -> float:
    """Units an extractor route moves each cycle: heads times one cycle of
    the hourly planning yield. Route quantity in a template is per cycle, not
    per hour (PI_PLAN 6A.1). At a 1 h cycle the two numbers match; a 2 h
    cycle must carry twice the hourly figure or half the output stays in the
    extractor."""
    per_cycle = float(per_head_per_hour) * max(int(cycle_seconds), 1) / 3600.0
    return float(max(1, int(heads * per_cycle)))


def per_head_per_hour(qty_per_cycle: int, cycle_seconds: int, program_seconds: float, heads: int) -> float:
    """Average units per head per hour of a real program, with noise - the
    calibration figure (P-54/P-64)."""
    if heads <= 0 or program_seconds <= 0:
        return 0.0
    total = program_total(qty_per_cycle, cycle_seconds, program_seconds, noise=True)
    return total / (program_seconds / 3600.0) / heads
