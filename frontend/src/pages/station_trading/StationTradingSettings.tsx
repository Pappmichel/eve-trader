import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, SimpleGrid, NumberInput, Button, Center, Loader, Table } from '@mantine/core'

import { stationTradingApi } from '../../api/client'
import type { StationTradingSettings as StationTradingSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { HintCard } from '../../components/HintCard'
import { pct } from '../../format'

export default function StationTradingSettings() {
  const { data } = useQuery({ queryKey: ['station-trading', 'settings'], queryFn: stationTradingApi.settings })
  const { data: skills } = useQuery({ queryKey: ['station-trading', 'skills'], queryFn: stationTradingApi.skills })

  const [form, setForm] = useState<StationTradingSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const save = useAction('Save Settings', stationTradingApi.updateSettings, [['station-trading', 'settings']])

  if (!form) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof StationTradingSettingsT>(key: K, value: StationTradingSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  return (
    <Stack maw={800}>
      <HintCard>
        Changes take effect immediately. Broker fee/sales tax below are starting defaults for the base-game NPC
        rate (no standings/skill reduction applied) - check your own in-game Market window and adjust. The
        Skills panel shows your live-pulled trade skill levels for reference, but doesn't automatically feed
        into these two fields (see the Add Character consent text for what's read).
      </HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Station</Title>
      <SimpleGrid cols={2}>
        <NumberInput label="Trade hub station ID" description="Default: Jita IV - Moon 4 - Caldari Navy Assembly Plant"
          value={form.station_id} min={1} onChange={(v) => set('station_id', Number(v))} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Economy</Title>
      <SimpleGrid cols={2}>
        <NumberInput label="Broker fee" suffix="%" decimalScale={2} value={form.broker_fee_rate * 100} min={0} max={100} step={0.1}
          onChange={(v) => set('broker_fee_rate', Number(v) / 100)} />
        <NumberInput label="Sales tax" suffix="%" decimalScale={2} value={form.sales_tax_rate * 100} min={0} max={100} step={0.1}
          onChange={(v) => set('sales_tax_rate', Number(v) / 100)} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Candidate Discovery</Title>
      <SimpleGrid cols={2}>
        <NumberInput label="Minimum spread" suffix="%" decimalScale={2} value={form.min_spread_threshold * 100} min={0} max={100} step={1}
          onChange={(v) => set('min_spread_threshold', Number(v) / 100)} />
        <NumberInput label="Minimum avg daily volume" value={form.min_daily_volume} min={0} step={1}
          onChange={(v) => set('min_daily_volume', Number(v))} />
      </SimpleGrid>

      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Settings
      </Button>

      <Title order={6} c="dimmed" tt="uppercase" mt="xl">Skills (live, per trader character)</Title>
      {!skills || skills.length === 0 ? (
        <Text size="xs" c="dimmed">No trader characters registered yet - see Add Character on the left.</Text>
      ) : (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Character</Table.Th>
              <Table.Th>Order Slots</Table.Th>
              <Table.Th>Trade</Table.Th>
              <Table.Th>Retail</Table.Th>
              <Table.Th>Wholesale</Table.Th>
              <Table.Th>Tycoon</Table.Th>
              <Table.Th>Accounting</Table.Th>
              <Table.Th>Broker Relations</Table.Th>
              <Table.Th>Adv. Broker Relations</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {skills.map((s) => (
              <Table.Tr key={s.character_name}>
                <Table.Td>{s.character_name}</Table.Td>
                {s.error ? (
                  <Table.Td colSpan={8}><Text size="xs" c="dimmed">{s.error}</Text></Table.Td>
                ) : (
                  <>
                    <Table.Td>{s.order_slots}</Table.Td>
                    <Table.Td>{s.levels?.Trade ?? 0}</Table.Td>
                    <Table.Td>{s.levels?.Retail ?? 0}</Table.Td>
                    <Table.Td>{s.levels?.Wholesale ?? 0}</Table.Td>
                    <Table.Td>{s.levels?.Tycoon ?? 0}</Table.Td>
                    <Table.Td>{s.levels?.Accounting ?? 0}</Table.Td>
                    <Table.Td>{s.levels?.['Broker Relations'] ?? 0}</Table.Td>
                    <Table.Td>{s.levels?.['Advanced Broker Relations'] ?? 0}</Table.Td>
                  </>
                )}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <Text size="xs" c="dimmed">
        Effective spread needed to clear fees, base game: {pct(form.broker_fee_rate * 2 + form.sales_tax_rate)}{' '}
        (broker fee on both legs + sales tax on the sell leg) - Broker Relations/Accounting reduce this in-game,
        not reflected here (see hint above).
      </Text>
    </Stack>
  )
}
