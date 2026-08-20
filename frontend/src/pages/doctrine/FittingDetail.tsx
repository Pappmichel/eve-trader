import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Stack, Title, Text, Table, Badge, Group, Grid, Card, CopyButton, Button, Collapse, NumberInput, Modal,
} from '@mantine/core'
import { useParams } from 'react-router-dom'
import { IconCopy, IconCheck, IconPencil } from '@tabler/icons-react'

import { doctrineApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { isk, dateTime } from '../../format'
import { EftFittingForm } from './EftFittingForm'

function EditFittingModal({ fittingId, initialRawEft, initialFuelBayText, initialShipMaintenanceBayText,
  opened, onClose }: {
  fittingId: string
  initialRawEft: string
  initialFuelBayText: string
  initialShipMaintenanceBayText: string
  opened: boolean
  onClose: () => void
}) {
  const save = useAction('Save Fitting', (args: { rawEft: string; fuelBayText: string; shipMaintenanceBayText: string }) =>
    doctrineApi.updateFitting(fittingId, {
      raw_eft: args.rawEft, fuel_bay_text: args.fuelBayText || null,
      ship_maintenance_bay_text: args.shipMaintenanceBayText || null,
    }), [['doctrine', 'status'], ['doctrine', 'fitting-detail', fittingId]])

  return (
    <Modal opened={opened} onClose={onClose} title="Edit Fitting" size="lg">
      {/* key=opened resets EftFittingForm's own internal state each time the modal reopens */}
      <EftFittingForm key={String(opened)} initialRawEft={initialRawEft} initialFuelBayText={initialFuelBayText}
        initialShipMaintenanceBayText={initialShipMaintenanceBayText}>
        {({ rawEft, fuelBayText, shipMaintenanceBayText }) => (
          <Button onClick={() => save.mutate({ rawEft, fuelBayText, shipMaintenanceBayText },
            { onSuccess: () => onClose() })} loading={save.isPending}>
            Save Changes
          </Button>
        )}
      </EftFittingForm>
    </Modal>
  )
}

const SEVERITY_COLOR: Record<string, string> = { critical: 'danger', tolerable: 'warn', info: 'dimmed' }
const AMPEL_COLOR: Record<string, string> = { green: 'accent', yellow: 'warn', red: 'danger', gray: 'dimmed' }

const SECTION_ORDER = ['low', 'med', 'high', 'rig', 'subsystem', 'service', 'drone', 'cargo', 'charge',
  'fuelbay', 'shipmaintenancebay']
const SECTION_LABEL: Record<string, string> = {
  low: 'Low Slots', med: 'Mid Slots', high: 'High Slots', rig: 'Rigs', subsystem: 'Subsystems',
  service: 'Services', drone: 'Drones', cargo: 'Cargo', charge: 'Charges (loaded)',
  fuelbay: 'Fuel Bay', shipmaintenancebay: 'Ship Maintenance Bay',
}

export default function FittingDetail() {
  const { fittingId } = useParams<{ fittingId: string }>()
  const [showRaw, setShowRaw] = useState(false)
  const [editOpen, setEditOpen] = useState(false)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['doctrine', 'fitting-detail', fittingId],
    queryFn: () => doctrineApi.fittingDetail(fittingId!),
    enabled: !!fittingId,
  })

  const [contractTarget, setContractTarget] = useState<number | null>(null)
  const [stockpileTarget, setStockpileTarget] = useState<number | null>(null)
  const saveTargets = useAction('Save Targets', () => doctrineApi.updateFitting(fittingId!, {
    contract_target: contractTarget, stockpile_target: stockpileTarget,
  }), [['doctrine', 'status'], ['doctrine', 'fitting-detail', fittingId ?? '']])

  if (!fittingId) return null
  if (isLoading) return <Text c="dimmed">Loading…</Text>
  if (!data) return <Text c="dimmed">Fitting not found.</Text>

  const { fitting, items, issues, contracts, status } = data
  const itemsBySection: Record<string, typeof items> = {}
  for (const it of items) {
    (itemsBySection[it.slot_section] ??= []).push(it)
  }
  const issuesByLine: Record<number, string[]> = {}
  for (const iss of issues) {
    (issuesByLine[iss.line_no] ??= []).push(iss.message)
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={4}>{fitting.name}</Title>
        <Group gap="xs">
          <Badge color={AMPEL_COLOR[status.contract_status] ?? 'dimmed'} variant="light">
            Contracts: {status.valid_contracts}/{status.contract_target}
          </Badge>
          <Badge color={AMPEL_COLOR[status.stockpile_status] ?? 'dimmed'} variant="light">
            Stockpile: {status.stockpile_status}
          </Badge>
          <Button size="xs" variant="default" leftSection={<IconPencil size={14} />} onClick={() => setEditOpen(true)}>
            Edit
          </Button>
        </Group>
      </Group>

      <EditFittingModal fittingId={fittingId} initialRawEft={fitting.raw_eft}
        initialFuelBayText={fitting.fuel_bay_text ?? ''} initialShipMaintenanceBayText={fitting.ship_maintenance_bay_text ?? ''}
        opened={editOpen} onClose={() => setEditOpen(false)} />

      <Group>
        <NumberInput label="Contract target" defaultValue={fitting.contract_target} min={0}
          onChange={(v) => setContractTarget(Number(v))} w={160} />
        <NumberInput label="Stockpile target" defaultValue={fitting.stockpile_target} min={0}
          onChange={(v) => setStockpileTarget(Number(v))} w={160} />
        <Button mt={24} variant="default" size="xs" onClick={() => saveTargets.mutate()} loading={saveTargets.isPending}>
          Save
        </Button>
      </Group>

      <Grid>
        <Grid.Col span={6}>
          <Card withBorder>
            <Title order={6} c="dimmed" tt="uppercase" mb="sm">Fitting ({items.length} items)</Title>
            <Stack gap="sm">
              {SECTION_ORDER.filter((s) => itemsBySection[s]?.length).map((section) => (
                <div key={section}>
                  <Text size="xs" fw={600} c="dimmed" tt="uppercase">{SECTION_LABEL[section]}</Text>
                  {itemsBySection[section].map((it) => (
                    <div key={`${it.line_no}-${it.type_id}`}>
                      <Text size="sm">
                        {it.type_name} {it.quantity !== 1 ? `x${it.quantity}` : ''} {it.is_offline ? '(offline)' : ''}
                      </Text>
                      {issuesByLine[it.line_no]?.map((msg, i) => (
                        <Text key={i} size="xs" c="warn">⚠ {msg}</Text>
                      ))}
                    </div>
                  ))}
                </div>
              ))}
            </Stack>

            <Button variant="subtle" size="xs" mt="md" onClick={() => setShowRaw((v) => !v)}>
              {showRaw ? 'Hide' : 'Show'} original EFT text
            </Button>
            <Collapse expanded={showRaw}>
              <Group justify="flex-end" mb={4}>
                <CopyButton value={fitting.raw_eft}>
                  {({ copied, copy }) => (
                    <Button size="xs" variant="subtle" leftSection={copied ? <IconCheck size={12} /> : <IconCopy size={12} />} onClick={copy}>
                      {copied ? 'Copied' : 'Copy'}
                    </Button>
                  )}
                </CopyButton>
              </Group>
              <Text component="pre" size="xs" style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>
                {fitting.raw_eft}
              </Text>
            </Collapse>
          </Card>
        </Grid.Col>

        <Grid.Col span={6}>
          <Card withBorder>
            <Title order={6} c="dimmed" tt="uppercase" mb="sm">Contracts ({contracts.length})</Title>
            {contracts.length === 0 && <Text size="sm" c="dimmed">No contracts matched to this fitting yet.</Text>}
            <Stack gap="md">
              {contracts.map((c) => (
                <div key={c.contract_id}>
                  <Group justify="space-between">
                    <Text size="sm">{c.title ?? `Contract ${c.contract_id}`}</Text>
                    <Badge color={c.validation_status === 'valid' ? 'accent' : c.validation_status === 'tolerable' ? 'warn' : 'danger'}
                      variant="light">
                      {c.validation_status}
                    </Badge>
                  </Group>
                  <Text size="xs" c="dimmed">{isk(c.price)} — expires {dateTime(c.date_expired)}</Text>
                  {c.deviations.length > 0 && (
                    <Table mt="xs" fz="xs">
                      <Table.Tbody>
                        {c.deviations.map((d, i) => (
                          <Table.Tr key={i}>
                            <Table.Td>{d.type_name}</Table.Td>
                            <Table.Td>{d.kind}</Table.Td>
                            <Table.Td>{d.expected_qty} soll / {d.actual_qty} ist</Table.Td>
                            <Table.Td><Badge size="xs" color={SEVERITY_COLOR[d.severity] ?? 'dimmed'} variant="light">{d.severity}</Badge></Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  )}
                </div>
              ))}
            </Stack>
          </Card>
        </Grid.Col>
      </Grid>
      <Button variant="subtle" size="xs" onClick={() => refetch()}>Refresh</Button>
    </Stack>
  )
}
