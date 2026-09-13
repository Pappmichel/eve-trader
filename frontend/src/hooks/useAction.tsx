import { useMutation, useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'
import { ApiError } from '../api/client'
import { ActionTierIcon, TIER_COPY, type ActionNetworkTier } from '../components/ActionTierIcon'

export type { ActionNetworkTier } from '../components/ActionTierIcon'

// Success toasts summarize small/flat results (e.g. {"added": 3, "removed": 1}
// from a Refresh/Sync action) inline, since there's often no other UI showing
// that number - but a large/nested result (a material tree, a full candidate
// list) already renders in the page itself right below, so dumping its raw
// JSON into the toast is just noise; past this length it's cut for a plain
// "Done" instead.
const MAX_RESULT_MESSAGE_LENGTH = 200

// Error messages are the opposite case: unlike a success result, the message
// itself (e.g. a backend ConfigError - "structure_sell_haircut: 5.0 is above
// the maximum allowed value (1)") IS the useful content, so collapsing to a
// generic placeholder the way the success path does would throw away exactly
// what the user needs to see. Truncated with an ellipsis instead - keeps the
// (usually most informative) start of the message, just bounds how much a
// single Mantine notification (sized for a line or two of text) has to hold.
const MAX_ERROR_MESSAGE_LENGTH = 300

function truncate(message: string, max: number): string {
  return message.length > max ? `${message.slice(0, max)}…` : message
}

// Optional per-action metadata (2026-09-13, "which buttons trigger ESI
// calls?" transparency pass) - `effect` is a one-sentence, user-facing
// description of what the action actually does (not just its terse button
// label), `tier` says whether it ever leaves this app's own database (see
// ActionTierIcon's own docstring for the three tiers). Both are optional so
// existing call sites keep compiling/behaving identically until migrated -
// this was rolled out to every action button in the app in one pass (see
// project memory "button action transparency plan"), not left half-done,
// but the fields stay optional rather than required so a future new button
// doesn't hard-fail to compile if someone forgets one.
export interface ActionMeta {
  tier?: ActionNetworkTier
  effect?: string
}

// Runs a mutation, shows a success/error notification, and invalidates the
// given query keys so dependent views refetch. Also builds a `tooltip`
// string and a `tierIcon` element from `meta` - callers wrap their own
// <Button> in `<Tooltip label={x.tooltip} disabled={!x.tooltip}>` and pass
// `leftSection={x.tierIcon}`, same shape as any other useMutation field
// (isPending, mutate, ...), just two more of them.
export function useAction<TArgs = void, TResult = unknown>(
  label: string,
  fn: (args: TArgs) => Promise<TResult>,
  invalidateKeys: string[][] = [],
  meta: ActionMeta = {},
) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      const summary = typeof result === 'object' ? JSON.stringify(result) : String(result ?? 'OK')
      notifications.show({
        title: label,
        message: summary.length > MAX_RESULT_MESSAGE_LENGTH ? 'Done' : summary,
        color: 'accent',
      })
      for (const key of invalidateKeys) {
        queryClient.invalidateQueries({ queryKey: key })
      }
    },
    onError: (err: unknown) => {
      const message = err instanceof ApiError ? err.message : String(err)
      notifications.show({ title: `${label} - Error`, message: truncate(message, MAX_ERROR_MESSAGE_LENGTH), color: 'danger' })
    },
  })
  const tier = meta.tier ?? 'local'
  // Local actions with no custom `effect` get no tooltip at all (e.g. a
  // plain "Save Settings" button needs no extra explanation) - every
  // cached/live action always gets at least the tier's own generic copy,
  // since that's exactly the signal this pass exists to add.
  const tooltipParts = [meta.effect, tier !== 'local' ? TIER_COPY[tier] : undefined].filter(Boolean)
  return Object.assign(mutation, {
    tooltip: tooltipParts.length ? tooltipParts.join(' ') : undefined,
    tierIcon: <ActionTierIcon tier={tier} />,
  })
}

// Shared `onSuccess` callback for a mutation whose result may carry a
// `priced_via_fallback` flag (Refresh Shortlist, Refresh Ore Shortlist,
// Reprocessing Quote) - see esi_client.ESIClient.structure_order_stats_
// bulk_or_goonmetrics's own docstring for why this exists. A second, extra
// notification alongside useAction's own generic success toast, since that
// one collapses a result this large to a plain "Done" and would otherwise
// bury this warning entirely.
export function warnIfPricedViaFallback(result: unknown): void {
  if (result && typeof result === 'object' && (result as Record<string, unknown>).priced_via_fallback) {
    notifications.show({
      title: 'Structure prices used the Goonmetrics fallback',
      message: 'No seller was logged in, or the real order book was unavailable - prices are a less precise '
        + 'community snapshot (best bid/ask, not a real order-book percentile) until this refreshes normally.',
      color: 'warn',
    })
  }
}
