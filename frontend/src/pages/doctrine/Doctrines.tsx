import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, Badge, Button, Group, Modal, TextInput, Textarea } from '@mantine/core'
import { Link } from 'react-router-dom'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi } from '../../api/client'
import type { DoctrineStatus } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { HintCard } from '../../components/HintCard'
import { DataTable } from '../../components/DataTable'

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

export default function Doctrines() {
  const { data, isLoading, dataUpdatedAt } = useQuery({ queryKey: ['doctrine', 'status'], queryFn: doctrineApi.listDoctrines })
  const [modalOpen, setModalOpen] = useState(false)

  const columns = useMemo<ColumnDef<DoctrineStatus, any>[]>(() => [
    {
      header: 'Name', accessorKey: 'doctrine_name', size: 220,
      cell: (i) => <Link to={`/doctrine/${i.row.original.doctrine_id}`}>{i.getValue()}</Link>,
    },
    { header: 'Fittings', accessorFn: (d) => d.fittings.length, id: 'fittings_count', size: 100 },
    {
      header: 'Contracts', id: 'contracts', size: 130,
      accessorFn: (d) => d.fittings.reduce((acc, f) => acc + f.valid_contracts, 0),
      cell: (i) => {
        const d = i.row.original
        const validSum = d.fittings.reduce((acc, f) => acc + f.valid_contracts, 0)
        const targetSum = d.fittings.reduce((acc, f) => acc + f.contract_target, 0)
        return <AmpelBadge status={d.contract_rollup} label={`${validSum}/${targetSum}`} />
      },
    },
    {
      header: 'Stockpile', accessorKey: 'stockpile_rollup', size: 120,
      cell: (i) => <AmpelBadge status={i.getValue()} label={i.getValue()} />,
    },
    {
      header: 'Overall', accessorKey: 'overall', size: 120,
      cell: (i) => <AmpelBadge status={i.getValue()} label={i.getValue()} />,
    },
  ], [])

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={4}>Doctrines</Title>
        <Button onClick={() => setModalOpen(true)}>New Doctrine</Button>
      </Group>

      <HintCard>
        Ampels: green = target met, yellow = partially covered (or only tolerable matches), red = no viable
        contracts/stock, gray = no data yet (never synced, or no asset-scanning character has synced yet).
      </HintCard>

      {!isLoading && (data?.length ?? 0) === 0 && <Text c="dimmed">No doctrines yet - create one to get started.</Text>}

      {(isLoading || (data?.length ?? 0) > 0) && (
        <DataTable
          data={data ?? []}
          columns={columns}
          tableId="doctrine-doctrines"
          exportFilename="doctrines"
          getRowId={(d) => d.doctrine_id}
          isLoading={isLoading}
          dataUpdatedAt={dataUpdatedAt}
        />
      )}

      <CreateDoctrineModal opened={modalOpen} onClose={() => setModalOpen(false)} />
    </Stack>
  )
}
