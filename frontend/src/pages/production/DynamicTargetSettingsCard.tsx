import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, NumberInput, SimpleGrid, Switch, Text, Title } from '@mantine/core'

import { productionApi } from '../../api/client'
import type { DynamicTargetSettings } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { DYNAMIC_KEYS, DYNAMIC_SETTINGS_KEY } from './DynamicTargetsPanel'

/**
 * Settings of the dynamic stock targets (docs/DYNAMIC_STOCK_TARGETS_PLAN.md section 9). Saved through its own
 * endpoint: POST /settings saves every field of the main form, which would make each ordinary save look like a
 * change of these values (review findings V-01/V-06).
 */
export function DynamicTargetSettingsCard() {
  const { data } = useQuery({ queryKey: DYNAMIC_SETTINGS_KEY, queryFn: productionApi.dynamicTargetSettings })
  const [form, setForm] = useState<DynamicTargetSettings | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])
  const save = useAction('Save Dynamic Target Settings',
    (s: DynamicTargetSettings) => productionApi.updateDynamicTargetSettings(s), DYNAMIC_KEYS)

  if (!form) return null
  const set = <K extends keyof DynamicTargetSettings>(key: K, value: DynamicTargetSettings[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  return (
    <Card withBorder mt="lg">
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">Dynamic Targets</Title>
      <Text size="xs" c="dimmed" mb="sm">
        Home market targets of rows set to dynamic follow demand and margin, frozen once per day. Saving here drops
        today's computed values; the next plan run (or Recalculate on the Stock Targets page) computes them again.
      </Text>
      <Switch label="Dynamic targets on" checked={form.dynamic_targets_enabled} mb="sm"
        onChange={(e) => set('dynamic_targets_enabled', e.currentTarget.checked)} />
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <NumberInput label="History window (days)" description="Own sales and market history; at most 28 (the market history source's limit)."
          value={form.dynamic_target_window_days} min={7} max={28} step={1}
          onChange={(v) => set('dynamic_target_window_days', Number(v))} />
        <NumberInput label="Minimum own history (days)" description="Own sales are only used once the ledger covers this many days."
          value={form.dynamic_target_min_history_days} min={1} max={28} step={1}
          onChange={(v) => set('dynamic_target_min_history_days', Number(v))} />
        <NumberInput label="Coverage (days of demand)" value={form.dynamic_target_coverage_days} min={1} max={120} step={1}
          onChange={(v) => set('dynamic_target_coverage_days', Number(v))} />
        <NumberInput label="Market share assumed sellable" suffix="%" decimalScale={1}
          value={form.dynamic_target_market_share * 100} min={0} max={100} step={1}
          onChange={(v) => set('dynamic_target_market_share', Number(v) / 100)} />
        <NumberInput label="Reference margin" suffix="%" decimalScale={1} description="Margin at which the profitability factor is 1.0 (must exceed the minimum margin)."
          value={form.dynamic_target_reference_margin * 100} min={0} step={1}
          onChange={(v) => set('dynamic_target_reference_margin', Number(v) / 100)} />
        <NumberInput label="Smallest factor" decimalScale={2} value={form.dynamic_target_factor_min} min={0} step={0.05}
          onChange={(v) => set('dynamic_target_factor_min', Number(v))} />
        <NumberInput label="Largest factor" decimalScale={2} value={form.dynamic_target_factor_max} min={0} step={0.1}
          onChange={(v) => set('dynamic_target_factor_max', Number(v))} />
        <NumberInput label="Smoothing" decimalScale={2} description="Share of the new value taken per run (0.01-1)."
          value={form.dynamic_target_smoothing} min={0.01} max={1} step={0.05}
          onChange={(v) => set('dynamic_target_smoothing', Number(v))} />
        <NumberInput label="Dead band" suffix="%" decimalScale={1} description="Moves smaller than this stay put."
          value={form.dynamic_target_deadband * 100} min={0} max={100} step={1}
          onChange={(v) => set('dynamic_target_deadband', Number(v) / 100)} />
        <NumberInput label="Single-day cap (x mean day)" decimalScale={1} description="One bulk day counts at most this many mean days of sales; 0 = off."
          value={form.dynamic_target_day_cap_multiple} min={0} step={0.5}
          onChange={(v) => set('dynamic_target_day_cap_multiple', Number(v))} />
      </SimpleGrid>
      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Dynamic Target Settings
      </Button>
    </Card>
  )
}
