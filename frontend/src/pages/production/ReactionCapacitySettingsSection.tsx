import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { NumberInput, SimpleGrid, Switch, Text, TextInput, Title } from '@mantine/core'

import { productionApi } from '../../api/client'
import type { ProductionSettings as ProductionSettingsT } from '../../api/types'
import { formatEveUtc, fromUtcInputValue, toUtcInputValue } from './reactionWindowTime'

type SetFn = <K extends keyof ProductionSettingsT>(key: K, value: ProductionSettingsT[K]) => void

/**
 * Reaction capacity settings (docs/REACTION_CAPACITY_PLAN.md section 5).
 * Saved with the main Production Settings form; the read-only slot summary
 * comes from GET /settings/reaction-capacity.
 */
export function ReactionCapacitySettingsSection({
  form,
  set,
}: {
  form: ProductionSettingsT
  set: SetFn
}) {
  const { data: status } = useQuery({
    queryKey: ['production', 'reaction-capacity-status'],
    queryFn: productionApi.reactionCapacityStatus,
  })

  const statusLines = useMemo(() => {
    if (!status) return null
    const derived = status.slot_override == null
      ? (status.derived_slots ?? status.slots_total)
      : status.derived_slots
    return {
      derived: derived ?? '–',
      busy: status.slots_busy,
      free: status.slots_free,
      capacity: status.capacity_slot_days,
      cycle: status.cycle_days,
      windowAnchor: status.window_anchor ?? form.reaction_window_next,
      windowNext: status.window_next,
    }
  }, [status, form.reaction_window_next])

  return (
    <>
      <Title order={6} c="dimmed" tt="uppercase" mt="md">Reaction capacity</Title>
      <Text size="xs" c="dimmed" mb="sm">
        When on, both Build Lists solve reaction jobs against free slots at the next window instead of
        expanding every reaction product as an unlimited build. Off = today&apos;s plans unchanged.
        Saving a capacity field marks the cached plans stale — recompute to apply.
      </Text>
      <Switch
        label="Reaction capacity planning"
        checked={form.reaction_capacity_enabled}
        data-testid="reaction-capacity-enabled"
        onChange={(e) => set('reaction_capacity_enabled', e.currentTarget.checked)}
        mb="sm"
      />
      <Switch
        label="Charge raw inputs taken from stock"
        description="A reaction that uses moon materials or fuel blocks the next cycle needs again pays their buy price. Stock above that need, and intermediates, stay free. Off = all stock is free to use."
        checked={form.reaction_stock_carry_value}
        data-testid="reaction-stock-carry-value"
        onChange={(e) => set('reaction_stock_carry_value', e.currentTarget.checked)}
        mb="sm"
      />
      <Switch
        label="Next-window demand from own sales"
        description="The next window plans for one cycle of own market sales (sales per day × window interval), not the whole backlog again. Items without sales keep the backlog. Without a usable sales ledger the backlog is used."
        checked={form.reaction_w2_from_sales}
        data-testid="reaction-w2-from-sales"
        onChange={(e) => set('reaction_w2_from_sales', e.currentTarget.checked)}
        mb="sm"
      />
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <NumberInput
          label="Max job duration (days)"
          description="Time from the window start until the reaction output must be done (for hauling). Runs per job = floor(duration × 86400 / seconds per run)."
          value={form.reaction_cycle_days}
          min={1}
          max={30}
          step={1}
          decimalScale={1}
          onChange={(v) => set('reaction_cycle_days', Number(v))}
        />
        <NumberInput
          label="Slot override"
          description="Empty = sum of reaction slots from producer characters (minus excluded)."
          placeholder="Derived"
          value={form.reaction_slot_override ?? ''}
          min={0}
          step={1}
          onChange={(v) => set('reaction_slot_override', v === '' ? null : Number(v))}
        />
        <TextInput
          label="Next window (anchor)"
          description="EVE time (UTC). Stored as an anchor; the effective next window rolls forward by the interval once it has passed."
          type="datetime-local"
          value={toUtcInputValue(form.reaction_window_next)}
          data-testid="reaction-window-next"
          onChange={(e) => set('reaction_window_next', fromUtcInputValue(e.currentTarget.value))}
        />
        <NumberInput
          label="Window interval (days)"
          description="Gap from one window to the next (also rolls a past anchor forward)."
          value={form.reaction_window_interval_days}
          min={1}
          step={1}
          decimalScale={1}
          onChange={(v) => set('reaction_window_interval_days', Number(v))}
        />
        <NumberInput
          label="Formula payback (cycles)"
          description="A formula purchase must pay for itself within this many cycles."
          value={form.reaction_formula_payback_cycles}
          min={0.01}
          step={0.5}
          decimalScale={2}
          onChange={(v) => set('reaction_formula_payback_cycles', Number(v))}
        />
        <NumberInput
          label="Next-window demand factor"
          description="Scale factor for the next window's demand (1.0 = one cycle of sales, or the backlog without sales)."
          value={form.reaction_next_window_demand_factor}
          min={0}
          step={0.1}
          decimalScale={2}
          onChange={(v) => set('reaction_next_window_demand_factor', Number(v))}
        />
      </SimpleGrid>
      {statusLines && (
        <Text size="xs" c="dimmed" mt="sm" data-testid="reaction-capacity-status">
          Derived slots: {statusLines.derived}
          {' · '}Busy at window: {statusLines.busy}
          {' · '}Free: {statusLines.free}
          {' · '}Capacity: {Number(statusLines.capacity).toFixed(1)} slot-days / {statusLines.cycle}-day max job
          {' · '}Anchor: {formatEveUtc(statusLines.windowAnchor)}
          {' · '}Effective next: {formatEveUtc(statusLines.windowNext)}
        </Text>
      )}
    </>
  )
}
