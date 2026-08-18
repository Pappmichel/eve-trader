import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, Table, Badge, Button, Group, Modal, TextInput, Textarea } from '@mantine/core'
import { Link } from 'react-router-dom'

import { doctrineApi } from '../../api/client'
import type { DoctrineStatus } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { HintCard } from '../../components/HintCard'

const AMPEL_COLOR: Record<string, string> = { green: 'accent', yellow: 'warn', red: 'danger', gray: 'dimmed' }

function AmpelBadge({ status, label }: { status: string; label: string }) {
  return <Badge color={AMPEL_COLOR[status] ?? 'dimmed'} variant="light">{label}</Badge>
}

function CreateDoctrineModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const create = useAction('Create Doctrine', () => doctrineApi.createDoctrine(name, description || null), [['doctrine', 'status']])

  return (
    <Modal opened={opened} onClose={onClose} title="New Doctrine">
      <Stack>
        <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
        <Textarea label="Description" value={description} onChange={(e) => setDescription(e.currentTarget.value)} minRows={2} />
        <Button
          onClick={() => create.mutate(undefined, { onSuccess: () => { onClose(); setName(''); setDescription('') } })}
          loading={create.isPending} disabled={!name.trim()}
        >
          Create
        </Button>
      </Stack>
    </Modal>
  )
}

function DoctrineRow({ d }: { d: DoctrineStatus }) {
  const validSum = d.fittings.reduce((acc, f) => acc + f.valid_contracts, 0)
  const targetSum = d.fittings.reduce((acc, f) => acc + f.contract_target, 0)
  return (
    <Table.Tr>
      <Table.Td>
        <Link to={`/doctrine/${d.doctrine_id}`}>{d.doctrine_name}</Link>
      </Table.Td>
      <Table.Td>{d.fittings.length}</Table.Td>
      <Table.Td>
        <Group gap="xs" wrap="nowrap">
          <AmpelBadge status={d.contract_rollup} label={`${validSum}/${targetSum}`} />
        </Group>
      </Table.Td>
      <Table.Td><AmpelBadge status={d.stockpile_rollup} label={d.stockpile_rollup} /></Table.Td>
      <Table.Td><AmpelBadge status={d.overall} label={d.overall} /></Table.Td>
    </Table.Tr>
  )
}

export default function Doctrines() {
  const { data, isLoading } = useQuery({ queryKey: ['doctrine', 'status'], queryFn: doctrineApi.listDoctrines })
  const [modalOpen, setModalOpen] = useState(false)

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={4}>Doctrines</Title>
        <Button onClick={() => setModalOpen(true)}>New Doctrine</Button>
      </Group>

      <HintCard>
        Ampels: green = target met, yellow = partially covered (or only tolerable matches), red = no viable
        contracts/stock, gray = no data yet (never synced, or Production hasn't synced assets).
      </HintCard>

      {isLoading && <Text c="dimmed">Loading…</Text>}
      {!isLoading && (data?.length ?? 0) === 0 && <Text c="dimmed">No doctrines yet - create one to get started.</Text>}

      {(data?.length ?? 0) > 0 && (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Fittings</Table.Th>
              <Table.Th>Contracts</Table.Th>
              <Table.Th>Stockpile</Table.Th>
              <Table.Th>Overall</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data!.map((d) => <DoctrineRow key={d.doctrine_id} d={d} />)}
          </Table.Tbody>
        </Table>
      )}

      <CreateDoctrineModal opened={modalOpen} onClose={() => setModalOpen(false)} />
    </Stack>
  )
}
