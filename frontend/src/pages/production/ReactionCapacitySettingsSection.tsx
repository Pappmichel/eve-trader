import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { NumberInput, SimpleGrid, Switch, Text, TextInput, Title } from '@mantine/core'

import { productionApi } from '../../api/client'
import type { ProductionSettings as ProductionSettingsT } from '../../api/types'

/** Convert a stored ISO datetime to the value shape ``datetime-local`` expects. */
function toLocalInputValue(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Convert a ``datetime-local`` value back to an ISO string (or null). */
function fromLocalInputValue(local: string): string | null {
  const trimmed = local.trim()
  if (!trimmed) return null
  const d = new Date(trimmed)
  if (Number.isNaN(d.getTime())) return null
  return d.toISOString()
}

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
    }
  }, [status])

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
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <NumberInput
          label="Cycle length (days)"
          description="Runs that fit in one job = floor(cycle days × 86400 / seconds per run)."
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
          label="Next window"
          description="ISO datetime of the next reaction start window. Empty = no date (incoming jobs use the live stock split)."
          type="datetime-local"
          value={toLocalInputValue(form.reaction_window_next)}
          data-testid="reaction-window-next"
          onChange={(e) => set('reaction_window_next', fromLocalInputValue(e.currentTarget.value))}
        />
        <NumberInput
          label="Window interval (days)"
          description="Gap from this window to the next (W2 preview)."
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
          description="Scale factor for W2 demand (1.0 = same as this window)."
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
          {' · '}Capacity: {Number(statusLines.capacity).toFixed(1)} slot-days / {statusLines.cycle}-day cycle
        </Text>
      )}
    </>
  )
}
