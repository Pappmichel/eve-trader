import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Button, Center, Loader, NumberInput, Select, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core'

import { piApi } from '../../api/client'
import type { PiSettings as PiSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { HintCard } from '../../components/HintCard'
import { HubSelect } from '../../components/HubSelect'
import { CC_OPTIONS, QueryError, ZONE_OPTIONS } from './common'

type NumKey = {
  [K in keyof PiSettingsT]: PiSettingsT[K] extends number ? K : never
}[keyof PiSettingsT]

export default function PiSettings() {
  const { data, error } = useQuery({ queryKey: ['pi', 'settings'], queryFn: piApi.settings })
  const { data: chars, error: charsError } = useQuery({ queryKey: ['pi', 'characters'], queryFn: piApi.characters })
  const [form, setForm] = useState<PiSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])
  const save = useAction('Save Settings', (s: PiSettingsT) => piApi.updateSettings(s), [
    ['pi', 'settings'], ['pi', 'profitability'], ['pi', 'demand'], ['pi', 'characters'],
  ])

  if (error) return <QueryError error={error} />
  if (!form) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof PiSettingsT>(key: K, value: PiSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  const num = (key: NumKey, label: string, opts: { min?: number; max?: number; step?: number; description?: string; decimals?: number } = {}) => (
    <NumberInput label={label} description={opts.description} value={form[key]} min={opts.min ?? 0} max={opts.max}
      step={opts.step} decimalScale={opts.decimals} onChange={(v) => v !== '' && set(key, Number(v) as never)} />
  )
  const percent = (key: NumKey, label: string, description?: string) => (
    <NumberInput label={label} description={description} suffix="%" decimalScale={2} min={0} max={100} step={1}
      value={form[key] * 100} onChange={(v) => v !== '' && set(key, (Number(v) / 100) as never)} />
  )

  return (
    <Stack maw={900}>
      <HintCard>Changes apply to every PI page once saved. Fee and skill values are entered manually.</HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Market hub</Title>
      <HubSelect label="Market hub" description="Where PI commodities are priced (independent of the other tools)"
        allowAll value={form.hub_region_id} onChange={(v) => set('hub_region_id', v)} />

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Fees and valuation</Title>
      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }}>
        {percent('pi_broker_fee_rate', 'Broker fee')}
        {percent('pi_sales_tax_rate', 'Sales tax')}
        <Select label="Valuation" allowDeselect={false} value={form.pi_valuation}
          data={[{ value: 'sell_orders', label: 'Sell orders (list outputs)' }, { value: 'buy_orders', label: 'Buy orders (sell instantly)' }]}
          onChange={(v) => v && set('pi_valuation', v as PiSettingsT['pi_valuation'])} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Freight and customs</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        {num('pi_freight_per_m3', 'Freight (ISK/m³)', { description: 'Hub to planets and back', step: 10 })}
        {percent('pi_owner_tax_rate', 'Customs owner rate', 'The player-owner part; the NPC part is added automatically')}
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Extraction</Title>
      <SimpleGrid cols={{ base: 1, xs: 2, sm: 4 }}>
        {num('pi_yield_highsec', 'Yield high-sec (P0/head/h)', { step: 100 })}
        {num('pi_yield_lowsec', 'Yield low-sec', { step: 100 })}
        {num('pi_yield_nullsec', 'Yield null-sec', { step: 100 })}
        {num('pi_yield_wormhole', 'Yield wormhole', { step: 100 })}
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }}>
        <Select label="Default zone" allowDeselect={false} data={ZONE_OPTIONS} value={form.pi_zone}
          onChange={(v) => v && set('pi_zone', v as PiSettingsT['pi_zone'])} />
        {num('pi_program_hours', 'Program length (hours)', { min: 1, max: 336 })}
        {num('pi_collection_interval_hours', 'Collection interval (hours)', { min: 1, max: 336 })}
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Verdict</Title>
      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }}>
        {num('pi_amortisation_days', 'Amortisation (days)', { description: 'Setup cost is spread over this many days', min: 1 })}
        {num('pi_min_isk_per_planet_day', 'Minimum ISK per planet per day', { step: 100000 })}
        {percent('pi_market_share_warning', 'Market share warning', 'Share of daily volume above which the market is "too thin"')}
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Manual fallbacks</Title>
      <Text size="xs" c="dimmed">Used for characters whose skills are not shared with Planetary Industry, and when no character is available.</Text>
      <SimpleGrid cols={{ base: 1, xs: 2, sm: 4 }}>
        {num('pi_planets_per_character', 'Planets per character', { min: 1, max: 6 })}
        {num('pi_characters', 'Characters', { min: 1, max: 100 })}
        <Select label="Command Center level" allowDeselect={false} data={CC_OPTIONS} value={String(form.pi_cc_level)}
          onChange={(v) => v && set('pi_cc_level', Number(v))} />
        {num('pi_customs_code_expertise_level', 'Customs Code Expertise level', { max: 5 })}
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Reference and demand</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        {num('pi_reference_radius_km', 'Reference radius (km)', { description: 'Planet size for factory chains in Profitability', min: 50, max: 200000 })}
        {num('pi_demand_days', 'Demand window (days)', { description: 'Production demand: colonies needed to cover the buy list in this time', min: 1 })}
      </SimpleGrid>

      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>Save Settings</Button>

      <Title order={6} c="dimmed" tt="uppercase" mt="xl">Characters</Title>
      <Text size="xs" c="dimmed">
        Share &quot;Skills&quot; with Planetary Industry on the Characters page to fill planets, Command Center level and
        Customs Code Expertise from ESI instead of the manual values above.
      </Text>
      {charsError ? <QueryError error={charsError} /> : null}
      {chars && (
        <>
          <Table withRowBorders={false} verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Character</Table.Th><Table.Th>Source</Table.Th><Table.Th ta="right">Planets</Table.Th>
                <Table.Th ta="right">CC level</Table.Th><Table.Th ta="right">Customs Code Expertise</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {chars.characters.map((c) => (
                <Table.Tr key={c.character_id}>
                  <Table.Td>{c.character_name ?? c.character_id}</Table.Td>
                  <Table.Td><Badge variant="light" color={c.source === 'esi' ? 'accent' : 'gray'}>{c.source}</Badge></Table.Td>
                  <Table.Td ta="right">{c.planets}</Table.Td>
                  <Table.Td ta="right">{c.cc_level}</Table.Td>
                  <Table.Td ta="right">{c.customs_code_expertise}</Table.Td>
                </Table.Tr>
              ))}
              {chars.characters.length === 0 && (
                <Table.Tr><Table.Td colSpan={5}><Text c="dimmed" size="sm">No character with a token; manual values apply.</Text></Table.Td></Table.Tr>
              )}
            </Table.Tbody>
          </Table>
          <Text size="sm">Total planet slots: <b>{chars.total_slots}</b> (highest CC level {chars.max_cc_level})</Text>
        </>
      )}
    </Stack>
  )
}
