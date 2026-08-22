import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Stack, Title, Text, Badge, Button, Group, Modal, NumberInput, ActionIcon,
} from '@mantine/core'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { IconCheck, IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { doctrineApi } from '../../api/client'
import type { FittingStatus } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { HintCard } from '../../components/HintCard'
import { DataTable } from '../../components/DataTable'
import { isk } from '../../format'
import { EftFittingForm } from './EftFittingForm'

const AMPEL_COLOR: Record<string, string> = { green: 'accent', yellow: 'warn', red: 'danger', gray: 'dimmed' }

function AmpelBadge({ status, label }: { status: string; label: string }) {
  return <Badge color={AMPEL_COLOR[status] ?? 'dimmed'} variant="light">{label}</Badge>
}

function AddFittingModal({ doctrineId, opened, onClose }: { doctrineId: string; opened: boolean; onClose: () => void }) {
  const [contractTarget, setContractTarget] = useState(0)
  const [stockpileTarget, setStockpileTarget] = useState(0)

  const add = useAction('Add Fitting', (args: { rawEft: string; fuelBayText: string; shipMaintenanceBayText: string }) =>
    doctrineApi.addFitting(doctrineId, {
      raw_eft: args.rawEft, contract_target: contractTarget, stockpile_target: stockpileTarget,
      fuel_bay_text: args.fuelBayText || null, ship_maintenance_bay_text: args.shipMaintenanceBayText || null,
    }), [['doctrine', 'status'], ['doctrine', 'doctrine-detail', doctrineId]])

  const reset = () => { setContractTarget(0); setStockpileTarget(0) }

  return (
    <Modal opened={opened} onClose={() => { onClose(); reset() }} title="New Fitting" size="lg">
      {/* key=opened resets EftFittingForm's own internal state each time the modal reopens */}
      <EftFittingForm key={String(opened)}>
        {({ rawEft, fuelBayText, shipMaintenanceBayText }) => (
          <>
            <Group grow>
              <NumberInput label="Contract target" value={contractTarget} min={0} onChange={(v) => setContractTarget(Number(v))} />
              <NumberInput label="Stockpile target (sets)" value={stockpileTarget} min={0} onChange={(v) => setStockpileTarget(Number(v))} />
            </Group>
            <Button onClick={() => add.mutate({ rawEft, fuelBayText, shipMaintenanceBayText },
              { onSuccess: () => { onClose(); reset() } })} loading={add.isPending}>
              Save Fitting
            </Button>
          </>
        )}
      </EftFittingForm>
    </Modal>
  )
}

// Inline target editing (GitHub issue #2) - reuses the exact same
// doctrineApi.updateFitting call FittingDetail.tsx's own target form does,
// just without navigating there first. Same "local state + dirty-check +
// Save button only shown once changed" pattern as AdminPage.tsx's
// UserToolCheckboxes.
function TargetEditor({ fittingId, contractTarget, stockpileTarget, doctrineId }: {
  fittingId: string
  contractTarget: number
  stockpileTarget: number
  doctrineId: string
}) {
  const [contract, setContract] = useState(contractTarget)
  const [stockpile, setStockpile] = useState(stockpileTarget)
  const dirty = contract !== contractTarget || stockpile !== stockpileTarget
  const save = useAction('Save Targets',
    () => doctrineApi.updateFitting(fittingId, { contract_target: contract, stockpile_target: stockpile }),
    [['doctrine', 'status'], ['doctrine', 'doctrine-detail', doctrineId]])

  return (
    <Group gap={4} wrap="nowrap">
      <NumberInput value={contract} onChange={(v) => setContract(Number(v))} min={0} w={64} size="xs"
        aria-label="Contract target" />
      <Text size="xs" c="dimmed">/</Text>
      <NumberInput value={stockpile} onChange={(v) => setStockpile(Number(v))} min={0} w={64} size="xs"
        aria-label="Stockpile target" />
      {dirty && (
        <ActionIcon size="sm" variant="filled" color="accent" aria-label="Save targets"
          onClick={() => save.mutate()} loading={save.isPending}>
          <IconCheck size={14} />
        </ActionIcon>
      )}
    </Group>
  )
}

export default function DoctrineDetail() {
  const { doctrineId } = useParams<{ doctrineId: string }>()
  const navigate = useNavigate()
  const [modalOpen, setModalOpen] = useState(false)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['doctrine', 'doctrine-detail', doctrineId],
    queryFn: () => doctrineApi.status(doctrineId),
    enabled: !!doctrineId,
  })
  const doctrine = data?.doctrines[0]

  const deleteDoctrine = useAction('Delete Doctrine', doctrineApi.deleteDoctrine, [['doctrine', 'status']])
  const deleteFitting = useAction('Delete Fitting', doctrineApi.deleteFitting, [
    ['doctrine', 'status'], ['doctrine', 'doctrine-detail', doctrineId ?? ''],
  ])

  const columns = useMemo<ColumnDef<FittingStatus, any>[]>(() => [
    {
      header: 'Fitting', accessorKey: 'fitting_name', size: 200,
      cell: (i) => <Link to={`/doctrine/fittings/${i.row.original.fitting_id}`}>{i.getValue()}</Link>,
    },
    { header: 'Hull', accessorKey: 'hull_name', size: 160 },
    {
      header: 'Contracts', id: 'contracts', size: 150,
      accessorFn: (f) => f.valid_contracts,
      cell: (i) => {
        const f = i.row.original
        return f.last_synced_at
          ? <AmpelBadge status={f.contract_status} label={`${f.valid_contracts}/${f.contract_target}`} />
          : <Badge color="dimmed" variant="light">not synced yet</Badge>
      },
    },
    {
      header: 'Stockpile', accessorKey: 'stockpile_status', size: 150,
      cell: (i) => {
        const f = i.row.original
        return f.assets_available
          ? <AmpelBadge status={f.stockpile_status} label={f.stockpile_status} />
          : <Badge color="dimmed" variant="light">no asset data</Badge>
      },
    },
    {
      header: 'Targets (Contract / Stockpile)', id: 'targets', size: 190,
      cell: (i) => {
        const f = i.row.original
        return (
          <TargetEditor fittingId={f.fitting_id} contractTarget={f.contract_target}
            stockpileTarget={f.stockpile_target} doctrineId={f.doctrine_id} />
        )
      },
    },
    {
      header: 'Multibuy', accessorKey: 'multibuy_cost', size: 130,
      cell: (i) => (i.getValue() != null ? isk(i.getValue()) : '–'),
    },
    {
      header: '', id: 'actions', size: 60, enableSorting: false,
      cell: (i) => (
        <ActionIcon size="sm" variant="subtle" color="danger" onClick={() => deleteFitting.mutate(i.row.original.fitting_id)}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [deleteFitting])

  if (!doctrineId) return null
  if (isLoading) return <Text c="dimmed">Loading…</Text>
  if (isError) return (
    <Stack align="flex-start" gap="xs">
      <Text c="dimmed">Failed to load this doctrine.</Text>
      <Button size="xs" variant="default" onClick={() => refetch()}>Retry</Button>
    </Stack>
  )
  if (!doctrine) return <Text c="dimmed">Doctrine not found.</Text>

  return (
    <Stack>
      <Group justify="space-between">
        <div>
          <Title order={4}>{doctrine.doctrine_name}</Title>
        </div>
        <Group>
          <Button onClick={() => setModalOpen(true)}>Add Fitting</Button>
          <Button variant="default" color="danger" onClick={() => deleteDoctrine.mutate(doctrineId, {
            onSuccess: () => navigate('/doctrine'),
          })} loading={deleteDoctrine.isPending}>
            Delete Doctrine
          </Button>
        </Group>
      </Group>

      {doctrine.fittings.length === 0 && <HintCard>No fittings yet - click "Add Fitting" to paste an EFT export.</HintCard>}

      {doctrine.fittings.length > 0 && (
        <DataTable
          data={doctrine.fittings}
          columns={columns}
          tableId="doctrine-detail-fittings"
          exportFilename={`doctrine-${doctrine.doctrine_name}-fittings`}
          getRowId={(f) => f.fitting_id}
        />
      )}

      <AddFittingModal doctrineId={doctrineId} opened={modalOpen} onClose={() => setModalOpen(false)} />
    </Stack>
  )
}
