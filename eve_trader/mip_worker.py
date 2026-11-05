"""Runs one HiGHS mixed-integer solve in its own process, so the caller can kill it.

HiGHS can stop honouring its `time_limit` (seen on aarch64 production with
large mineral lists: stuck in HighsRedcostFixing::addRootRedcost for minutes
on an input x86 solves in 0.3 s). A thread stuck in native code cannot be
stopped from Python and would hold a CPU until the service restarts, so
mineral_supply_lp starts this module with a hard deadline instead.

Protocol: pickled linprog keyword arguments on stdin, a pickled
{"x": ndarray or None, "message": str} on stdout. Trusted input only: the
parent process wrote it. Imports nothing from the app beyond this module.
"""
from __future__ import annotations

import pickle
import sys


def main() -> None:
    from scipy.optimize import linprog
    kwargs = pickle.load(sys.stdin.buffer)
    result = linprog(**kwargs)
    pickle.dump({"x": result.x, "message": result.message}, sys.stdout.buffer)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
