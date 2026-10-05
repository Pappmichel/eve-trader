import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Group, Select, Stack, Table, Text } from '@mantine/core'

import { piApi } from '../../api/client'
import type { PiChainNode, PiZone } from '../../api/types'
import { isk } from '../../format'
import { CC_OPTIONS, Loading, QueryError, SectionTitle, ZONE_OPTIONS, num, reasonLabel, usePiMeta, usePiSettings, zoneLabel } from './common'

function flatten(node: PiChainNode, depth: number, out: Array<{ node: PiChainNode; depth: number }>) {
  out.push({ node, depth })
  node.children.forEach((c) => flatten(c, depth + 1, out))
  return out
}

export default function Chains() {
  const { data: meta, error: metaError } = usePiMeta()
  const [product, setProduct] = useState<string | null>(null)
  const [zone, setZone] = useState<PiZone | null>(null)
  const [cc, setCc] = useState<string | null>(null)

  const settings = usePiSettings()
  const { data, isFetching, error } = useQuery({
    queryKey: ['pi', 'chain', product, zone, cc],
    queryFn: () => piApi.chain(Number(product), zone ?? undefined, cc === null ? undefined : Number(cc)),
    enabled: product !== null,
  })

  const items = (meta?.products ?? []).filter((p) => p.tier >= 2)
  const rows = data ? flatten(data.tree, 0, []) : []

  return (
    <Stack>
      {metaError ? <QueryError error={metaError} /> : null}
      <Group align="flex-end">
        <Select label="Product (P2-P4)" searchable w={300} value={product} onChange={setProduct}
          data={items.map((p) => ({ value: String(p.type_id), label: `${p.name} (P${p.tier})` }))} />
        <Select label="Security zone" w={200} clearable placeholder={`Settings default (${zoneLabel(settings?.pi_zone)})`} data={ZONE_OPTIONS}
          value={zone} onChange={(v) => setZone(v as PiZone | null)} />
        <Select label="Command Center level" w={180} clearable placeholder="Settings default" data={CC_OPTIONS}
          value={cc} onChange={setCc} />
      </Group>
      {isFetching && <Loading />}
      {error ? <QueryError error={error} /> : null}

      {data && !isFetching && (
        <Stack>
          {!data.feasible && <Alert color="warn">Part of this chain cannot be built with the current settings.</Alert>}
          <SectionTitle>Chain tree</SectionTitle>
          <Table withRowBorders={false} verticalSpacing={3}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Stage</Table.Th><Table.Th>Product</Table.Th>
                <Table.Th ta="right">Colonies needed</Table.Th>
                <Table.Th ta="right">Output per colony</Table.Th>
                <Table.Th ta="right">Profit per colony</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map(({ node, depth }, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{node.chain}</Table.Td>
                  <Table.Td style={{ paddingLeft: 8 + depth * 20 }}>{node.product_name} (P{node.tier})</Table.Td>
                  <Table.Td ta="right">
                    {node.colonies === null ? <Text span c="danger" size="sm">{reasonLabel(node.reason) || 'not possible'}</Text>
                      : `${num(node.colonies, 2)} (${node.colonies_ceil})`}
                  </Table.Td>
                  <Table.Td ta="right">{node.per_colony_per_hour === null ? '–' : `${num(node.per_colony_per_hour, 1)} /h`}</Table.Td>
                  <Table.Td ta="right">{node.profit_per_colony_day === null ? '–' : `${isk(node.profit_per_colony_day)}/day`}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          <SectionTitle>Stop at tier</SectionTitle>
          <Text size="xs" c="dimmed">Build the lower stages only and sell at that tier instead of processing further.</Text>
          <Table withRowBorders={false} verticalSpacing={3}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Sell at</Table.Th><Table.Th ta="right">Planets</Table.Th>
                <Table.Th ta="right">Profit / day</Table.Th><Table.Th ta="right">Profit / planet / day</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.stop_at.map((s) => (
                <Table.Tr key={s.tier}>
                  <Table.Td>P{s.tier}</Table.Td>
                  {s.feasible ? (
                    <>
                      <Table.Td ta="right">{num(s.planets, 2)} ({s.planets_ceil})</Table.Td>
                      <Table.Td ta="right">{isk(s.profit_per_day)}</Table.Td>
                      <Table.Td ta="right">{isk(s.profit_per_planet_day)}</Table.Td>
                    </>
                  ) : <Table.Td colSpan={3}><Text c="dimmed" size="sm">Not feasible</Text></Table.Td>}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Stack>
      )}
    </Stack>
  )
}
