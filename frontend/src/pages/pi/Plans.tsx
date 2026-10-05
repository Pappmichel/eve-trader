import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ActionIcon, Button, Group, Stack, Table, Text, TextInput } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'

import { piApi } from '../../api/client'
import type { PiPlan } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { dateTime } from '../../format'
import { HintCard } from '../../components/HintCard'
import { Loading, QueryError, num, usePiMeta } from './common'

function PlanRow({ plan, productName, typeName }: { plan: PiPlan; productName: string; typeName: string }) {
  const navigate = useNavigate()
  const [name, setName] = useState(plan.name)
  const [notes, setNotes] = useState(plan.notes ?? '')
  const update = useAction('Update plan', () => piApi.updatePlan(plan.plan_id, {
    name: name.trim(), design: plan.design, planet_id: plan.planet_id, planet_type_id: plan.planet_type_id,
    radius_km: plan.radius_km, character_id: plan.character_id, owner_tax_rate: plan.owner_tax_rate,
    freight_per_m3: plan.freight_per_m3, yield_override: plan.yield_override, notes: notes.trim() || null,
  }), [['pi', 'plans']])
  const remove = useAction('Delete plan', () => piApi.deletePlan(plan.plan_id), [['pi', 'plans']])
  const dirty = name !== plan.name || notes !== (plan.notes ?? '')

  return (
    <Table.Tr>
      <Table.Td><TextInput size="xs" aria-label="Plan name" value={name} onChange={(e) => setName(e.currentTarget.value)} /></Table.Td>
      <Table.Td>{productName} <Text span c="dimmed" size="xs">({plan.design.chain})</Text></Table.Td>
      <Table.Td>{typeName}, {num(plan.radius_km, 0)} km</Table.Td>
      <Table.Td><TextInput size="xs" aria-label="Plan notes" value={notes} onChange={(e) => setNotes(e.currentTarget.value)} /></Table.Td>
      <Table.Td>{dateTime(plan.updated_at)}</Table.Td>
      <Table.Td>
        <Group gap="xs" wrap="nowrap" justify="flex-end">
          <Button size="compact-xs" disabled={!dirty || !name.trim()} loading={update.isPending} onClick={() => update.mutate()}>Save</Button>
          <Button size="compact-xs" variant="default" onClick={() => navigate('/pi/planner', { state: { prefill: { plan } } })}>
            Open in Planner
          </Button>
          <ActionIcon size="sm" variant="subtle" color="danger" aria-label={`Delete ${plan.name}`}
            onClick={() => { if (window.confirm(`Delete plan "${plan.name}"?`)) remove.mutate() }}>
            <IconTrash size={14} />
          </ActionIcon>
        </Group>
      </Table.Td>
    </Table.Tr>
  )
}

export default function Plans() {
  const { data: meta } = usePiMeta()
  const { data, isLoading, error } = useQuery({ queryKey: ['pi', 'plans'], queryFn: piApi.plans })
  return (
    <Stack>
      <HintCard>Plans are saved from the Planner. Open one to continue editing or to generate its template.</HintCard>
      {error ? <QueryError error={error} /> : null}
      {isLoading ? <Loading /> : (
        <Table withRowBorders={false} verticalSpacing={4}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th><Table.Th>Product</Table.Th><Table.Th>Planet</Table.Th>
              <Table.Th>Notes</Table.Th><Table.Th>Updated</Table.Th><Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(data ?? []).map((p) => (
              <PlanRow key={p.plan_id} plan={p}
                productName={meta?.products.find((x) => x.type_id === p.design.product_type_id)?.name ?? String(p.design.product_type_id)}
                typeName={meta?.planet_types.find((x) => x.type_id === p.planet_type_id)?.name ?? String(p.planet_type_id)} />
            ))}
            {(data ?? []).length === 0 && <Table.Tr><Table.Td colSpan={6}><Text c="dimmed">No saved plans yet.</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  )
}
