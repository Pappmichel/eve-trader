# Phase G — Scale, performance & reliability (online)

Measure first. Do not optimize. Do not edit the frozen planner.

## G.1 — Scale baseline

Matrix: N = 1, 10, 50, 100 pooled `Finished Widget A × 2` on the Cloud Agent
VM against local Postgres. Timings are documentation, not SLOs. Re-run:

```
pytest tests/test_phase_g1_scale_baseline.py -s
```

Recorded 2026-09-07 (Cloud Agent VM, Python 3.12, local Postgres).

| N | create_ms | list_ms | one_ms | combined_ms | audit_ms | peak_kib |
|---|-----------|---------|--------|-------------|----------|----------|
| 1 | 5.33 | 2.63 | 12.01 | 6.46 | 2.80 | 12.6 |
| 10 | 26.07 | 4.08 | 9.30 | 10.08 | 4.53 | 12.4 |
| 50 | 123.54 | 15.28 | 6.74 | 31.75 | 16.01 | 11.1 |
| 100 | 199.95 | 32.42 | 8.58 | 60.20 | 36.89 | 15.5 |

H.2 re-run (same machine, same command):

| N | create_ms | list_ms | one_ms | combined_ms | audit_ms | peak_kib |
|---|-----------|---------|--------|-------------|----------|----------|
| 1 | 4.52 | 2.25 | 11.61 | 5.62 | 2.43 | 12.6 |
| 10 | 24.88 | 4.55 | 8.91 | 10.54 | 4.12 | 12.4 |
| 50 | 124.84 | 15.93 | 6.82 | 30.18 | 16.04 | 11.5 |
| 100 | 213.66 | 35.25 | 6.92 | 60.86 | 30.99 | 18.2 |

Semantic gates at the same scale: Combined qty = 2N, top-level hangar not
netted, Combined does not persist. Both runs passed those gates.

### Bottlenecks (observation only)

Same ranking as the local tool: create × N transactions; Combined loads N
orders then one planner call; list N+1 item_count queries. No optimization
in this phase.

## G.2 — Reliability

20 Edit → wrapper Compute → reload → Compute → Combined cycles. Hangar ESI
stock unchanged. One `item_set` per Set. (`tests/test_phase_g2_reliability.py`)

## G.3 — Failure recovery

Invalid create writes no header and no events. Injected exception after
header insert inside `batch_session()` rolls back. Audit reports planted
empty headers; delete keeps events.
(`tests/test_phase_g3_failure_recovery.py`)
