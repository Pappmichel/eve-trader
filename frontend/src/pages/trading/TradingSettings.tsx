import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, SimpleGrid, NumberInput, TextInput, Button, Center, Loader } from '@mantine/core'

import { tradingApi } from '../../api/client'
import type { TradingSettings as TradingSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { useStructureNameOptions } from '../../hooks/useStaticOptions'
import { HintCard } from '../../components/HintCard'
import { StructureIdField } from '../../components/StructureIdField'

export default function TradingSettings() {
  const { data } = useQuery({ queryKey: ['trading', 'settings'], queryFn: tradingApi.settings })
  const { data: structureNames } = useStructureNameOptions()
  const [form, setForm] = useState<TradingSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const save = useAction('Save Settings', tradingApi.updateSettings, [['trading', 'settings']])

  // Confirmed real gap: a bare `return null` here rendered a totally blank
  // page during the initial fetch, unlike every other page in the app which
  // shows a DataTable skeleton or this same Loader/Center while its primary
  // query is in flight - on a slow connection this looked broken, not loading.
  if (!form) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof TradingSettingsT>(key: K, value: TradingSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  return (
    <Stack maw={700}>
      <HintCard>Changes take effect immediately and are saved to <b>config.yaml</b> (survives a restart).</HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Economy</Title>
      <Text size="xs" c="dimmed">
        Freight and fee rates change with the market/carrier - adjust here if import/sale numbers suddenly look unrealistic.
      </Text>
      <SimpleGrid cols={2}>
        <NumberInput label="Freight cost Jita→structure (ISK/m³)" value={form.import_cost_per_m3} min={0} step={50}
          onChange={(v) => set('import_cost_per_m3', Number(v))} />
        <NumberInput label="Structure sell haircut (0-1)" value={form.structure_sell_haircut} min={0} max={1} step={0.01}
          onChange={(v) => set('structure_sell_haircut', Number(v))} />
        <NumberInput label="Minimum profit/unit (ISK)" value={form.min_profit_threshold} min={0} step={100}
          onChange={(v) => set('min_profit_threshold', Number(v))} />
        <NumberInput label="Minimum margin for 'Import'" value={form.min_margin_threshold} min={0} step={0.01}
          onChange={(v) => set('min_margin_threshold', Number(v))} />
        <NumberInput label="Grace period before deactivation (days)" value={form.skip_grace_period_days} min={0} step={1}
          onChange={(v) => set('skip_grace_period_days', Number(v))} />
        <NumberInput label="Max. active shortlist entries (when cap is on)" value={form.max_active_shortlist_items} min={1} step={10}
          onChange={(v) => set('max_active_shortlist_items', Number(v))} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Candidate Search</Title>
      <Text size="xs" c="dimmed">
        Categories are no longer pre-filtered - only margin/hit rate/volume decide whether an item gets suggested.
      </Text>
      <SimpleGrid cols={3}>
        <NumberInput label="Min. hit rate (0-1)" value={form.min_hit_rate} min={0} max={1} step={0.05}
          onChange={(v) => set('min_hit_rate', Number(v))} />
        <NumberInput label="Min. avg market movement (reference region)" value={form.min_avg_movement} min={0} step={1}
          onChange={(v) => set('min_avg_movement', Number(v))} />
        <NumberInput label="Safe mode: max IDs/run" value={form.safe_mode_max_ids} min={1} step={50}
          onChange={(v) => set('safe_mode_max_ids', Number(v))} />
        <NumberInput label="Trade reconciliation: days back" value={form.lookback_days} min={1} step={5}
          onChange={(v) => set('lookback_days', Number(v))} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Regions &amp; Structure</Title>
      <Text size="xs" c="dimmed">Only change if your trading location shifts entirely.</Text>
      <SimpleGrid cols={3}>
        <NumberInput label="Jita region ID" value={form.jita_region_id} min={1}
          onChange={(v) => set('jita_region_id', Number(v))} />
        <NumberInput label="Reference region ID" value={form.reference_region_id} min={1}
          onChange={(v) => set('reference_region_id', Number(v))} />
        <StructureIdField label="Structure ID" value={form.structure_id ?? null}
          onChange={(v) => set('structure_id', v)} structureNames={structureNames} />
      </SimpleGrid>
      <SimpleGrid cols={2} mt="xs">
        <TextInput label="Structure market slug (appraise.gnf.lt, optional failsafe)"
          description="Falls back to this Goonmetrics snapshot for structure pricing when no seller is logged in or ESI fails - not used by Undercut Check."
          value={form.structure_market_slug ?? ''}
          onChange={(e) => set('structure_market_slug', e.currentTarget.value)} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Characters</Title>
      <SimpleGrid cols={2}>
        <TextInput label="Buyer name (Jita)" value={form.buyer_character_name ?? ''} autoComplete="off"
          onChange={(e) => set('buyer_character_name', e.currentTarget.value)} />
        <TextInput label="Seller name (structure)" value={form.seller_character_name ?? ''} autoComplete="off"
          onChange={(e) => set('seller_character_name', e.currentTarget.value)} />
      </SimpleGrid>

      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Settings
      </Button>
    </Stack>
  )
}
