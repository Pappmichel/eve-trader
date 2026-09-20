import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, SimpleGrid, NumberInput, TextInput, TagsInput, Button, Center, Loader, MultiSelect } from '@mantine/core'

import { tradingApi } from '../../api/client'
import type { TradingSettings as TradingSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { useStructureNameOptions } from '../../hooks/useStaticOptions'
import { HintCard } from '../../components/HintCard'
import { StructureIdField } from '../../components/StructureIdField'

export default function TradingSettings() {
  const { data } = useQuery({ queryKey: ['trading', 'settings'], queryFn: tradingApi.settings })
  const { data: divisionOptions } = useQuery({
    queryKey: ['trading', 'wallet-division-options'],
    queryFn: tradingApi.walletDivisionOptions,
  })
  const { data: structureNames } = useStructureNameOptions()
  const [form, setForm] = useState<TradingSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const save = useAction('Save Settings', tradingApi.updateSettings, [['trading', 'settings']])

  // Confirmed real gap: a bare `return null` here rendered a totally blank
  // page during the initial fetch, unlike every other page in the app which
  // shows a DataTable skeleton or this same Loader/Center while its primary
  // query is in flight - on a slow connection this looked broken, not loading.
  if (!form || !divisionOptions) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof TradingSettingsT>(key: K, value: TradingSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  return (
    <Stack maw={700}>
      <HintCard>Changes take effect immediately and are saved to your account.</HintCard>

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
        <NumberInput label="Max. active shortlist entries (when cap is on)"
          description="Toggle the cap itself on the Shortlist page — easy to miss here, so the switch lives next to the live count."
          value={form.max_active_shortlist_items} min={1} step={10}
          onChange={(v) => set('max_active_shortlist_items', Number(v))} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Candidate Search</Title>
      <Text size="xs" c="dimmed">
        Beyond the excluded market-group paths below, only margin/hit rate/volume decide whether an item gets suggested -
        no keyword allow/denylist, no per-item size cap.
      </Text>
      <SimpleGrid cols={3}>
        <NumberInput label="Min. hit rate (0-1)" value={form.min_hit_rate} min={0} max={1} step={0.05}
          onChange={(v) => set('min_hit_rate', Number(v))} />
        <NumberInput label="Min. avg market movement (reference region)" value={form.min_avg_movement} min={0} step={1}
          onChange={(v) => set('min_avg_movement', Number(v))} />
        <NumberInput label="Safe mode: max IDs/run" value={form.safe_mode_max_ids} min={1} step={50}
          onChange={(v) => set('safe_mode_max_ids', Number(v))} />
        <NumberInput label="Max new shortlist items per run"
          description="Search + Add keeps leftover recommendations for the next run instead of dropping them."
          value={form.max_shortlist_growth_per_run} min={1} step={10}
          onChange={(v) => set('max_shortlist_growth_per_run', Number(v))} />
        <NumberInput label="Trade reconciliation: days back" value={form.lookback_days} min={1} step={5}
          onChange={(v) => set('lookback_days', Number(v))} />
      </SimpleGrid>
      <TagsInput mt="xs" label="Excluded market-group path prefixes"
        description="Top-level market-group paths (e.g. 'ships', 'blueprints') hard-excluded from candidate discovery entirely, regardless of profitability."
        value={form.excluded_path_prefixes} onChange={(v) => set('excluded_path_prefixes', v)}
        placeholder="Add a market-group path prefix" splitChars={[',']} />

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

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Corp wallets</Title>
      <Text size="xs" c="dimmed">
        Corp-funded market orders are recorded on the corporation wallet, not the placing character&apos;s
        personal wallet, so Reconcile Trades would miss them without this. Select which of the seven ESI
        wallet divisions to page. Leave empty to read every division (the default). The fetching character
        needs the Accountant or Junior Accountant role in that corp — not Director, and not Station Manager.
        A buyer or seller added before this scope existed needs a one-time re-login before corp fills appear.
        Corp fills are counted regardless of which member placed them; that is correct for a single-member
        corp. In a shared corp this would pull other members&apos; trades into realized profit and into
        average_daily_sold_by_type, which feeds Profit / Day on the shortlist.
      </Text>
      <MultiSelect label="Corp wallet divisions included in Reconcile Trades"
        data={divisionOptions.wallet_division_ids.map((id) => ({ value: String(id), label: `Division ${id}` }))}
        value={(form.wallet_division_ids ?? []).map(String)}
        onChange={(v) => set('wallet_division_ids', v.map(Number))}
        placeholder="All divisions" clearable />

      <Title order={6} c="dimmed" tt="uppercase" mt="md">ESI freshness</Title>
      <Text size="xs" c="dimmed">
        How often the background scheduler re-fetches each data kind, and how many
        missed intervals before a failed owner&apos;s snapshot is cleared. A manual
        Sync from any tool stamps freshness and pushes that owner × kind past the
        next scheduled fetch. Frequent: market orders and wallet. Normal: assets,
        jobs, contracts. Rare: blueprints and skills.
      </Text>
      <SimpleGrid cols={2}>
        <NumberInput label="Frequent interval (hours)" value={form.esi_frequent_interval_hours} min={0} step={0.5}
          onChange={(v) => set('esi_frequent_interval_hours', Number(v))} />
        <NumberInput label="Normal interval (hours)" value={form.esi_normal_interval_hours} min={0} step={0.5}
          onChange={(v) => set('esi_normal_interval_hours', Number(v))} />
        <NumberInput label="Rare interval (hours)" value={form.esi_rare_interval_hours} min={0} step={1}
          onChange={(v) => set('esi_rare_interval_hours', Number(v))} />
        <NumberInput label="Stale-clear multiples" value={form.esi_stale_clear_multiples} min={0} step={1}
          description="Clear an owner's snapshot after this many missed intervals of failed fetches."
          onChange={(v) => set('esi_stale_clear_multiples', Number(v))} />
      </SimpleGrid>

      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Settings
      </Button>
    </Stack>
  )
}
