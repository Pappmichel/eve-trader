import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, SimpleGrid, NumberInput, Button, Center, Loader } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { stationTradingApi } from '../../api/client'
import { changedFields } from '../../api/changedFields'
import type { StationTradingSettings as StationTradingSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { HubSelect } from '../../components/HubSelect'
import { FeeSkillCharacterSelect, FeeSkillNote, SkillFeeHint } from '../../components/FeeSkillHints'
import { ObservedRateHint } from '../../components/ObservedRateHint'
import { pct } from '../../format'

type SkillRow = NonNullable<Awaited<ReturnType<typeof stationTradingApi.skills>>>[number]

const skillLevel = (name: string): ColumnDef<SkillRow, any> => ({
  header: name === 'Advanced Broker Relations' ? 'Adv. Broker Relations' : name,
  id: name,
  size: 120,
  // A character whose skills could not be read shows "–" instead of a made-up 0.
  accessorFn: (r) => (r.error ? null : (r.levels?.[name] ?? 0)),
  cell: (i) => i.getValue() ?? '–',
})

const SKILL_COLUMNS: ColumnDef<SkillRow, any>[] = [
  { header: 'Character', accessorKey: 'character_name', size: 200 },
  {
    header: 'Order Slots', id: 'order_slots', size: 110,
    accessorFn: (r) => (r.error ? null : r.order_slots),
    cell: (i) => i.getValue() ?? '–',
  },
  ...['Trade', 'Retail', 'Wholesale', 'Tycoon', 'Accounting', 'Broker Relations', 'Advanced Broker Relations'].map(skillLevel),
  {
    header: 'Note', id: 'error', size: 260, enableSorting: false,
    accessorFn: (r) => r.error ?? '',
  },
]

// Main NPC trade station per hub region (ids checked against ESI
// /universe/stations: Jita IV - Moon 4 CNAP, Amarr VIII (Oris) EFA,
// Dodixie IX - Moon 20 FNAP, Rens VI - Moon 8 Brutor Tribe Treasury).
const HUB_STATIONS: Record<number, number> = {
  10000002: 60003760, 10000043: 60008494, 10000032: 60011866, 10000030: 60004588,
}

export default function StationTradingSettings() {
  const { data } = useQuery({ queryKey: ['station-trading', 'settings'], queryFn: stationTradingApi.settings })
  const { data: skills } = useQuery({ queryKey: ['station-trading', 'skills'], queryFn: stationTradingApi.skills })
  const { data: fees } = useQuery({ queryKey: ['station-trading', 'fee-suggestions'], queryFn: stationTradingApi.feeSuggestions })

  const [form, setForm] = useState<StationTradingSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const save = useAction('Save Settings', stationTradingApi.updateSettings,
    [['station-trading', 'settings'], ['station-trading', 'fee-suggestions']])

  if (!form) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof StationTradingSettingsT>(key: K, value: StationTradingSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))
  // A hint's "Use this value": the normal save with that one field changed.
  const apply = <K extends keyof StationTradingSettingsT>(key: K, value: StationTradingSettingsT[K]) => {
    set(key, value)
    save.mutate(changedFields(data, { ...form, [key]: value }))
  }
  const observed = fees?.observed
  const observedDetail = "In a shared corporation wallet this includes other members' sales. "
    + (observed?.relist_note ?? '')

  return (
    <Stack maw={800}>
      <HintCard>
        Changes take effect immediately. Broker fee/sales tax are your own values; the hints below each field
        show what the chosen character's skills and standings, and your wallet, imply - apply one with a click.
      </HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Station</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <HubSelect label="Market hub" description="Sets the region and its main trade station together"
          value={form.hub_region_id}
          onChange={(v) => setForm((f) => {
            if (!f) return f
            const station = HUB_STATIONS[v]
            return { ...f, hub_region_id: v, ...(station ? { station_id: station } : {}) }
          })} />
        <NumberInput label="Trade hub station ID" description="Set by the hub above (Jita: Jita IV - Moon 4 - Caldari Navy Assembly Plant); edit only for a custom station"
          value={form.station_id} min={1} onChange={(v) => set('station_id', Number(v))} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Economy</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <FeeSkillCharacterSelect hints={fees?.skills} value={form.fee_skill_character_id}
          onChange={(v) => apply('fee_skill_character_id', v)} />
        <FeeSkillNote />
        <Stack gap={4}>
          <NumberInput label="Broker fee" suffix="%" decimalScale={3} value={form.broker_fee_rate * 100} min={0} max={100} step={0.1}
            onChange={(v) => set('broker_fee_rate', Number(v) / 100)} />
          <SkillFeeHint testId="skill-broker-fee" kind="broker" hints={fees?.skills} part={fees?.skills?.broker_fee}
            label="From skills" current={form.broker_fee_rate} applying={save.isPending}
            onApply={(v) => apply('broker_fee_rate', v)} />
          {observed && (
            <ObservedRateHint testId="observed-broker-fee" label={`Actual last ${observed.window_days} days`}
              value={observed.suggested_broker_fee_rate} current={form.broker_fee_rate}
              format={(v) => `${(v * 100).toFixed(3)} %`} same={(a, b) => Math.abs(a - b) < 0.00005}
              sample={`${observed.sales} sales`} detail={`Broker fee / gross of the sell leg. ${observedDetail}`}
              emptyText={`${observed.sales} of ${observed.min_sales} sales needed`} emptyMessage={observed.message}
              onApply={(v) => apply('broker_fee_rate', v)} applying={save.isPending} />
          )}
        </Stack>
        <Stack gap={4}>
          <NumberInput label="Sales tax" suffix="%" decimalScale={3} value={form.sales_tax_rate * 100} min={0} max={100} step={0.1}
            onChange={(v) => set('sales_tax_rate', Number(v) / 100)} />
          <SkillFeeHint testId="skill-sales-tax" kind="tax" hints={fees?.skills} part={fees?.skills?.sales_tax}
            label="From skills" current={form.sales_tax_rate} applying={save.isPending}
            onApply={(v) => apply('sales_tax_rate', v)} />
          {observed && (
            <ObservedRateHint testId="observed-sales-tax" label={`Actual last ${observed.window_days} days`}
              value={observed.suggested_sales_tax_rate} current={form.sales_tax_rate}
              format={(v) => `${(v * 100).toFixed(3)} %`} same={(a, b) => Math.abs(a - b) < 0.00005}
              sample={`${observed.sales} sales`} detail={`Sales tax / gross. ${observedDetail}`}
              emptyText={`${observed.sales} of ${observed.min_sales} sales needed`} emptyMessage={observed.message}
              onApply={(v) => apply('sales_tax_rate', v)} applying={save.isPending} />
          )}
        </Stack>
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Candidate Discovery</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <NumberInput label="Minimum spread" suffix="%" decimalScale={2} value={form.min_spread_threshold * 100} min={0} max={100} step={1}
          onChange={(v) => set('min_spread_threshold', Number(v) / 100)} />
        <NumberInput label="Minimum avg daily volume" value={form.min_daily_volume} min={0} step={1}
          onChange={(v) => set('min_daily_volume', Number(v))} />
        <NumberInput label="Max. active shortlist entries (when cap is on)" value={form.max_active_shortlist_items} min={1} step={10}
          onChange={(v) => set('max_active_shortlist_items', Number(v))} />
      </SimpleGrid>
      <Text size="xs" c="dimmed">
        The cap itself is off by default - toggle it on the Shortlist page.
      </Text>

      <Button mt="md" w={240} onClick={() => save.mutate(changedFields(data, form))} loading={save.isPending}>
        Save Settings
      </Button>

      <Title order={6} c="dimmed" tt="uppercase" mt="xl">Skills (live, per trader character)</Title>
      {!skills || skills.length === 0 ? (
        <Text size="xs" c="dimmed">No trader characters registered yet - share Skills with Station Trading on the Characters page.</Text>
      ) : (
        <DataTable
          data={skills} columns={SKILL_COLUMNS} maxHeight={320}
          getRowId={(r) => r.character_name} exportFilename="station-trading-skills"
        />
      )}
      <Text size="xs" c="dimmed">
        Effective spread needed to clear fees: {pct(form.broker_fee_rate * 2 + form.sales_tax_rate)}{' '}
        (broker fee on both legs + sales tax on the sell leg), at the rates saved above.
      </Text>
    </Stack>
  )
}
