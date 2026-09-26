# Phase F — System-wide validation (online)

Core stays frozen. Phase F checks that E.2–E.5 do not interfere.

Deliverables: `tests/test_phase_f1_cross_feature.py`,
`tests/test_phase_f2_end_to_end.py`.

Operator path: create (optionally by name) → set item + auto-recompute
wrapper → save note / mark done → reload (`do_get_special_order`) →
Combined with another order → cost-index / slot overlay → Compute again.

Combined `net_against_stock` remains per-call (SF-5). Auto-recompute is
session-only (`?recompute=` / UI checkbox), not stored on the order.
