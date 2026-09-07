# Known limitations — Special-Order path (online)

- Combined preview is never saved (SF-2 / SF-4).
- Top-level ordered qty is never hangar-netted (SF-1).
- Missing market prices → unpriced Buy, no BOM expand.
- Auto-recompute is session-only.
- List still does one item_count query per header (G.1 observation).
- Combined loads each selected order's items then one planner call.
- Job-slot totals require ESI character-skills sync (`character_slots`).
- Invention on special orders is a preview, not a second logistics workflow.
