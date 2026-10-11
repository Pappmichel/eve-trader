# Known limitations — Special-Order path (online)

- Combined preview is never saved (SF-2 / SF-4).
- Top-level ordered qty is never hangar-netted in the order's own preview
  (SF-1). Integrated standing lists do net it (SF-9).
- Missing market prices → unpriced Buy, no BOM expand.
- Auto-recompute is session-only.
- List still does one item_count query per header (G.1 observation).
- Combined loads each selected order's items then one planner call.
- Job-slot totals require ESI character-skills sync (`character_slots`).
- Invention on special orders is a preview, not a second logistics workflow.
- Integrated two-pass planning rounds runs up per pass, so an item needed by
  both the order pass and the stock-target pass can get up to one extra run
  (documented in SPECIAL_ORDERS_INTEGRATION_PLAN.md D4).
- Marking an order `done` after delivery is still manual; `ready_to_deliver`
  on the Special Orders page is a hint only (SF-10).
- Invention Buy List rows from order demand are not attributed in
  `special_order_quantity` (manufacturing/reaction buys and builds are).
- Cached Build / Asset-Optimized plans do not recompute themselves when an
  integrated order's inclusion/items/status change - they are kept, flagged
  `stale`, and the pages show a recompute hint (D9).
