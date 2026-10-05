import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Alert, Badge, Button, Group, NumberInput, Select, SimpleGrid, Stack, Table, Text } from '@mantine/core'

import { piApi } from '../../api/client'
import type { PiSystemAnalysis, PiSystemHit } from '../../api/types'
import { notify } from '../../notify'
import { isk, pct } from '../../format'
import {
  CC_OPTIONS, Loading, SectionTitle, Stat, SystemSearch, VerdictBadge, iskPerDay, num,
} from './common'

export default function SystemAnalysis() {
  const navigate = useNavigate()
  const [system, setSystem] = useState<PiSystemHit | null>(null)
  const [slots, setSlots] = useState<number | string>('')
  const [characters, setCharacters] = useState<number | string>('')
  const [cc, setCc] = useState<string | null>(null)
  const [tax, setTax] = useState<number | string>('')
  const [result, setResult] = useState<PiSystemAnalysis | null>(null)

  const analyse = useMutation({
    mutationFn: () => {
      if (!system) throw new Error('Pick a system first')
      return piApi.systemAnalysis(system.solar_system_id, {
        slots: slots === '' ? undefined : Number(slots),
        characters: characters === '' ? undefined : Number(characters),
        cc_level: cc === null ? undefined : Number(cc),
        owner_tax_rate: tax === '' ? undefined : Number(tax) / 100,
      })
    },
    onSuccess: setResult,
    onError: (e) => notify({ title: 'System analysis failed', message: e instanceof Error ? e.message : String(e), color: 'danger' }),
  })

  const plan = result?.plan
  return (
    <Stack>
      <SystemSearch onPick={(h) => { setSystem(h); setResult(null) }} />
      <SimpleGrid cols={{ base: 1, xs: 2, sm: 4 }}>
        <NumberInput label="Planet slots" placeholder="from characters" min={1} max={60} value={slots} onChange={setSlots} />
        <NumberInput label="Characters" placeholder="from characters" min={1} max={100} value={characters} onChange={setCharacters} />
        <Select label="Command Center level" clearable placeholder="from characters" data={CC_OPTIONS} value={cc} onChange={setCc} />
        <NumberInput label="Owner customs tax" suffix="%" min={0} max={100} placeholder="Settings default" value={tax} onChange={setTax} />
      </SimpleGrid>
      <Group>
        <Button disabled={!system} loading={analyse.isPending} onClick={() => analyse.mutate()}>Analyse system</Button>
        {analyse.isPending && <Text size="sm" c="dimmed">Planning colonies - this can take a few seconds.</Text>}
      </Group>
      {analyse.isPending && <Loading />}

      {result && plan && (
        <Stack>
          <Group gap="sm">
            <Text fw={700} size="lg">{result.system.name}</Text>
            <Badge variant="outline">{result.zone}</Badge>
            <Badge variant="outline">security {num(result.system.security, 2)}</Badge>
            <Badge variant="outline">{result.slots} slots, {result.characters} character(s), CC {result.cc_level}</Badge>
          </Group>
          {result.cannot.map((c, i) => <Alert key={i} color="warn">{c}</Alert>)}

          <SectionTitle>Best plan</SectionTitle>
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <Stat label="Profit" value={iskPerDay(plan.profit_per_day)} />
            <Stat label="Profit per slot" value={iskPerDay(plan.profit_per_slot)} />
            <Stat label="Slots used" value={`${plan.used_slots} / ${result.slots}`} />
            <Stat label="Status" value={plan.status} />
            <Stat label="Best single uses" value={iskPerDay(plan.best_single_uses_profit_per_day)}
              hint="The same slots used for the best stand-alone colonies" />
            <Stat label="Chain gain" value={iskPerDay(plan.chain_gain_per_day)}
              hint="What letting colonies feed each other adds" />
          </SimpleGrid>
          {plan.notes.map((n, i) => <Text key={i} size="sm" c="dimmed">{n}</Text>)}

          <Table withRowBorders={false} verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Planet</Table.Th><Table.Th>Product</Table.Th><Table.Th>Chain</Table.Th>
                <Table.Th ta="right">Colonies</Table.Th><Table.Th>Yield factors</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {plan.colonies.map((c, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{c.planet_name ?? c.planet_id}</Table.Td>
                  <Table.Td>{c.product_name}</Table.Td>
                  <Table.Td>{c.chain}</Table.Td>
                  <Table.Td ta="right">{c.count}</Table.Td>
                  <Table.Td>{c.yield_factors.map((y) => pct(y)).join(', ') || '–'}</Table.Td>
                </Table.Tr>
              ))}
              {plan.colonies.length === 0 && <Table.Tr><Table.Td colSpan={5}><Text c="dimmed">No profitable colony found.</Text></Table.Td></Table.Tr>}
            </Table.Tbody>
          </Table>

          <SectionTitle>Flows (units per day)</SectionTitle>
          <Table withRowBorders={false} verticalSpacing={2}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Commodity</Table.Th><Table.Th ta="right">Produced</Table.Th><Table.Th ta="right">Consumed</Table.Th>
                <Table.Th ta="right">Internal</Table.Th><Table.Th ta="right">Sold</Table.Th><Table.Th ta="right">Bought</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {plan.flows.map((f) => (
                <Table.Tr key={f.type_id}>
                  <Table.Td>{f.name}</Table.Td>
                  <Table.Td ta="right">{num(f.produced, 0)}</Table.Td>
                  <Table.Td ta="right">{num(f.consumed, 0)}</Table.Td>
                  <Table.Td ta="right">{num(f.internal, 0)}</Table.Td>
                  <Table.Td ta="right">{num(f.sold, 0)}</Table.Td>
                  <Table.Td ta="right">{num(f.bought, 0)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          <SectionTitle>Best uses per planet</SectionTitle>
          {result.planets.map((p) => (
            <div key={p.planet_id}>
              <Text fw={600}>{p.name} <Text span c="dimmed" size="sm">{p.planet_type}, {num(p.radius_km, 0)} km</Text></Text>
              <Table withRowBorders={false} verticalSpacing={2}>
                <Table.Tbody>
                  {p.best.slice(0, 5).map((b) => (
                    <Table.Tr key={`${b.chain}:${b.product_type_id}`} style={{ cursor: 'pointer' }}
                      onClick={() => navigate('/pi/planner', {
                        state: { prefill: { chain: b.chain, product_type_id: b.product_type_id, planet_type_id: p.planet_type_id } },
                      })}>
                      <Table.Td>{b.product_name}</Table.Td>
                      <Table.Td>{b.chain}</Table.Td>
                      <Table.Td ta="right">{isk(b.profit_per_day)}/day</Table.Td>
                      <Table.Td><VerdictBadge worthIt={b.worth_it} reason={b.reason} /></Table.Td>
                    </Table.Tr>
                  ))}
                  {p.best.length === 0 && <Table.Tr><Table.Td><Text c="dimmed" size="sm">Nothing buildable.</Text></Table.Td></Table.Tr>}
                </Table.Tbody>
              </Table>
            </div>
          ))}
        </Stack>
      )}
    </Stack>
  )
}
