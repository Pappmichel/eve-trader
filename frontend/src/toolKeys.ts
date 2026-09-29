// Frontend copies of tool-key vocabularies that live on the backend. Kept in
// sync by hand (docs/CHARACTER_MANAGEMENT_PLAN.md, R12) - toolKeys.test.ts
// guards the drift that is checkable from inside the frontend.

// Mirrors access_gate.ALL_TOOL_KEYS (eve_trader/access_gate.py).
export const ALL_TOOL_KEYS = [
  'trading', 'production', 'doctrine', 'refining', 'station_trading', 'sorting',
  'portfolio', 'admin', 'characters', 'module_reprocessing', 'char_info',
] as const

// Tools that consume raw ESI data, i.e. the union of every `consumingTools` in
// esiRegistry.ts (== eve_trader.esi_data.registry.consuming_tool_keys()).
// Granting any of these auto-ticks `characters` in Admin's UI, since the
// Characters page is where their data access is configured. `portfolio` was
// missing here until the Character Management hub work (R12) even though it
// has been a real consumer since the Portfolio rework.
export const ESI_CONSUMING_TOOLS: readonly string[] = [
  'trading', 'production', 'doctrine', 'station_trading', 'sorting', 'portfolio', 'char_info',
]

// Sub-tools of the Character Management hub. The hub itself has no grant: its
// Landing card shows when the session holds any of these. New keys are added
// in the phase that ships their router (a dead grant in Admin only confuses).
export const CHARACTER_MANAGEMENT_TOOL_KEYS: readonly string[] = ['characters', 'char_info']

// `tools` is undefined while /api/gate/status has not loaded (or the gate is
// off and the backend returns every key) - "show everything", same convention
// as Landing's ToolCard.
export function hasAnyToolGrant(
  tools: readonly string[] | undefined, keys: readonly string[],
): boolean {
  if (tools === undefined) return true
  return keys.some((k) => tools.includes(k))
}
