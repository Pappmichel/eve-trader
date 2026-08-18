import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Stack, Title, Text, Table, Badge, Button, Group, Modal, Textarea, NumberInput, ActionIcon, Alert,
} from '@mantine/core'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { IconTrash } from '@tabler/icons-react'

import { doctrineApi } from '../../api/client'
import type { FittingStatus, ParsedFittingPreview } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { HintCard } from '../../components/HintCard'

const AMPEL_COLOR: Record<string, string> = { green: 'accent', yellow: 'warn', red: 'danger', gray: 'dimmed' }

function AmpelBadge({ status, label }: { status: string; label: string }) {
  return <Badge color={AMPEL_COLOR[status] ?? 'dimmed'} variant="light">{label}</Badge>
}

function AddFittingModal({ doctrineId, opened, onClose }: { doctrineId: string; opened: boolean; onClose: () => void }) {
  const [rawEft, setRawEft] = useState('')
  const [contractTarget, setContractTarget] = useState(0)
  const [stockpileTarget, setStockpileTarget] = useState(0)
  const [preview, setPreview] = useState<ParsedFittingPreview | null>(null)

  const parse = useAction('Parse Fitting', () => doctrineApi.parseFitting(rawEft))
  const add = useAction('Add Fitting', () => doctrineApi.addFitting(doctrineId, {
    raw_eft: rawEft, contract_target: contractTarget, stockpile_target: stockpileTarget,
  }), [['doctrine', 'status'], ['doctrine', 'doctrine-detail', doctrineId]])

  const reset = () => { setRawEft(''); setPreview(null); setContractTarget(0); setStockpileTarget(0) }

  return (
    <Modal opened={opened} onClose={() => { onClose(); reset() }} title="New Fitting" size="lg">
      <Stack>
        <Textarea label="EFT fitting text" placeholder="[Rifter, My Fit]&#10;..." value={rawEft}
          onChange={(e) => { setRawEft(e.currentTarget.value); setPreview(null) }} minRows={10} autosize maxRows={20}
          styles={{ input: { fontFamily: 'monospace' } }} />

        {!preview && (
          <Button variant="default" disabled={!rawEft.trim()} loading={parse.isPending}
            onClick={() => parse.mutate(undefined, { onSuccess: (r) => setPreview(r as ParsedFittingPreview) })}>
            Preview
          </Button>
        )}

        {preview && (
          <>
            <Alert color="accent" variant="light">
              Hull: <b>{preview.hull_name}</b> — {preview.items.length} item(s), {preview.issues.length} warning(s)
            </Alert>
            {preview.issues.length > 0 && (
              <Stack gap={4}>
                {preview.issues.map((iss, i) => (
                  <Text key={i} size="xs" c="warn">Line {iss.line_no}: {iss.message}</Text>
                ))}
              </Stack>
            )}
            <Group grow>
              <NumberInput label="Contract target" value={contractTarget} min={0} onChange={(v) => setContractTarget(Number(v))} />
              <NumberInput label="Stockpile target (sets)" value={stockpileTarget} min={0} onChange={(v) => setStockpileTarget(Number(v))} />
            </Group>
            <Button onClick={() => add.mutate(undefined, { onSuccess: () => { onClose(); reset() } })} loading={add.isPending}>
              Save Fitting
            </Button>
          </>
        )}
      </Stack>
    </Modal>
  )
}

function FittingRow({ f, onDelete }: { f: FittingStatus; onDelete: (id: string) => void }) {
  return (
    <Table.Tr>
      <Table.Td><Link to={`/doctrine/fittings/${f.fitting_id}`}>{f.fitting_name}</Link></Table.Td>
      <Table.Td>
        {f.last_synced_at ? (
          <AmpelBadge status={f.contract_status} label={`${f.valid_contracts}/${f.contract_target}`} />
        ) : (
          <Badge color="dimmed" variant="light">not synced yet</Badge>
        )}
      </Table.Td>
      <Table.Td>
        {f.assets_available ? (
          <AmpelBadge status={f.stockpile_status} label={f.stockpile_status} />
        ) : (
          <Badge color="dimmed" variant="light">no asset data</Badge>
        )}
      </Table.Td>
      <Table.Td>
        <ActionIcon size="sm" variant="subtle" color="danger" onClick={() => onDelete(f.fitting_id)}>
          <IconTrash size={14} />
        </ActionIcon>
      </Table.Td>
    </Table.Tr>
  )
}

export default function DoctrineDetail() {
  const { doctrineId } = useParams<{ doctrineId: string }>()
  const navigate = useNavigate()
  const [modalOpen, setModalOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['doctrine', 'doctrine-detail', doctrineId],
    queryFn: () => doctrineApi.status(doctrineId),
    enabled: !!doctrineId,
  })
  const doctrine = data?.doctrines[0]

  const deleteDoctrine = useAction('Delete Doctrine', doctrineApi.deleteDoctrine, [['doctrine', 'status']])
  const deleteFitting = useAction('Delete Fitting', doctrineApi.deleteFitting, [
    ['doctrine', 'status'], ['doctrine', 'doctrine-detail', doctrineId ?? ''],
  ])

  if (!doctrineId) return null
  if (isLoading) return <Text c="dimmed">Loading…</Text>
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
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Fitting</Table.Th>
              <Table.Th>Contracts</Table.Th>
              <Table.Th>Stockpile</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {doctrine.fittings.map((f) => (
              <FittingRow key={f.fitting_id} f={f} onDelete={(id) => deleteFitting.mutate(id)} />
            ))}
          </Table.Tbody>
        </Table>
      )}

      <AddFittingModal doctrineId={doctrineId} opened={modalOpen} onClose={() => setModalOpen(false)} />
    </Stack>
  )
}
