"""Sphere math for PI pins. Pins are (La, Lo): La is a *polar angle*
(pi/2 = equator), Lo the longitude, both in radians - the convention of the
game's templates (P-22, P-41). Distances are great-circle central angles;
never flat La/Lo differences, which shrink by sin(La) away from the equator.
"""
from __future__ import annotations

import math

EQUATOR = math.pi / 2


def central_angle(la1: float, lo1: float, la2: float, lo2: float) -> float:
    cos_d = math.cos(la1) * math.cos(la2) + math.sin(la1) * math.sin(la2) * math.cos(lo1 - lo2)
    return math.acos(max(-1.0, min(1.0, cos_d)))


def distance_km(la1: float, lo1: float, la2: float, lo2: float, radius_km: float) -> float:
    return central_angle(la1, lo1, la2, lo2) * radius_km


def offset(la: float, lo: float, d_polar: float, d_east: float) -> tuple[float, float]:
    """A point d_polar (towards larger La) and d_east (radians of arc, along
    the local parallel) away from (la, lo). Exact enough for the small
    offsets of a colony (a few hundredths of a radian)."""
    new_la = la + d_polar
    s = math.sin(new_la)
    new_lo = lo + (d_east / s if abs(s) > 1e-9 else 0.0)
    return new_la, new_lo
