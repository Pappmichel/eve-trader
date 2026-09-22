import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Accordion, Button, Container, Group, Stack, Table, Text, Title,
} from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'

import { adminApi } from '../../api/client'
import type {
  SdeChangedBlueprint, SdeChangedItem, SdeDiff, SdeDiffItem,
} from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { useBackgroundJob, useBackgroundJobStart } from '../../hooks/useBackgroundJob'

const SDE_RESULT_KEYS: string[][] = [['production', 'sde', 'counts'], ['production', 'sde-freshness']]
const SDE_LABELS = { sde_preview: 'Preview SDE' }

function byName(a: { name: string }, b: { name: string }) {
  return a.name.localeCompare(b.name)
}

function isSdeDiff(value: unknown): value is SdeDiff {
  if (typeof value !== 'object' || value === null) return false
  const v = value as SdeDiff
  return Array.isArray(v.new_items)
    && Array.isArray(v.removed_items)
    && Array.isArray(v.changed_items)
    && Array.isArray(v.changed_blueprints)
    && typeof v.table_deltas === 'object'
    && v.table_deltas !== null
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '–'
  return String(value)
}

function ItemList({ items }: { items: SdeDiffItem[] }) {
  if (items.length === 0) return <Text size="sm" c="dimmed">Keine.</Text>
  return (
    <Stack gap={4}>
      {items.map((item) => (
        <Text size="sm" key={item.type_id}>{item.name} ({item.type_id})</Text>
      ))}
    </Stack>
  )
}

function ChangedItemList({ items }: { items: SdeChangedItem[] }) {
  if (items.length === 0) return <Text size="sm" c="dimmed">Keine.</Text>
  return (
    <Stack gap="sm">
      {items.map((item) => (
        <div key={item.type_id}>
          <Text size="sm" fw={600}>{item.name} ({item.type_id})</Text>
          {Object.entries(item.changes).map(([field, pair]) => (
            <Text size="sm" c="dimmed" key={field}>
              {field}: {formatValue(pair[0])} → {formatValue(pair[1])}
            </Text>
          ))}
        </div>
      ))}
    </Stack>
  )
}

function BlueprintPanel({ bp }: { bp: SdeChangedBlueprint }) {
  return (
    <Stack gap="sm">
      {bp.materials.length > 0 && (
        <div>
          <Text size="sm" fw={600} mb={4}>Materialien</Text>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Material</Table.Th>
                <Table.Th>Alt</Table.Th>
                <Table.Th>Neu</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {bp.materials.map((row) => (
                <Table.Tr key={row.material_type_id}>
                  <Table.Td>{row.name}</Table.Td>
                  <Table.Td>{row.old_qty}</Table.Td>
                  <Table.Td>{row.new_qty}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
      )}
      {bp.products && (
        <div>
          <Text size="sm" fw={600} mb={4}>Produkte</Text>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Feld</Table.Th>
                <Table.Th>Alt</Table.Th>
                <Table.Th>Neu</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              <Table.Tr>
                <Table.Td>Menge</Table.Td>
                <Table.Td>{bp.products.old_qty}</Table.Td>
                <Table.Td>{bp.products.new_qty}</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        </div>
      )}
      {bp.time && (
        <div>
          <Text size="sm" fw={600} mb={4}>Bauzeit</Text>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Feld</Table.Th>
                <Table.Th>Alt</Table.Th>
                <Table.Th>Neu</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              <Table.Tr>
                <Table.Td>Zeit</Table.Td>
                <Table.Td>{bp.time.old}</Table.Td>
                <Table.Td>{bp.time.new}</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        </div>
      )}
      {bp.invention_probability && (
        <div>
          <Text size="sm" fw={600} mb={4}>Invention-Wahrscheinlichkeit</Text>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Feld</Table.Th>
                <Table.Th>Alt</Table.Th>
                <Table.Th>Neu</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              <Table.Tr>
                <Table.Td>Wahrscheinlichkeit</Table.Td>
                <Table.Td>{bp.invention_probability.old}</Table.Td>
                <Table.Td>{bp.invention_probability.new}</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        </div>
      )}
    </Stack>
  )
}

export default function SdePreviewPage() {
  const started = useRef(false)
  const [applied, setApplied] = useState(false)
  const sdeJob = useBackgroundJob({
    queryKey: ['admin', 'pipeline', 'sde-preview'],
    fetchStatus: adminApi.previewSdeStatus,
    resultKeys: SDE_RESULT_KEYS,
    labels: SDE_LABELS,
    defaultLabel: 'Preview SDE',
    pollIntervalMs: 1000,
  })
  const previewStart = useBackgroundJobStart(sdeJob, () => adminApi.previewSde())
  const apply = useAction('Änderungen übernehmen', async () => {
    const result = await adminApi.applySde()
    setApplied(true)
    return result
  }, SDE_RESULT_KEYS)

  const statusLoaded = sdeJob.status !== undefined
  const running = Boolean(sdeJob.runningStatus || previewStart.isPending)

  useEffect(() => {
    if (!statusLoaded || started.current) return
    started.current = true
    if (sdeJob.runningStatus) return
    previewStart.mutate()
    // Start once when the page is entered; mutate identity is not a retrigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusLoaded, sdeJob.runningStatus])

  const rawResult = sdeJob.status?.result
  const diff = isSdeDiff(rawResult) ? rawResult : null
  const canApply = sdeJob.status?.status === 'succeeded' && diff != null && !applied && !running

  const newItems = useMemo(() => [...(diff?.new_items ?? [])].sort(byName), [diff])
  const removedItems = useMemo(() => [...(diff?.removed_items ?? [])].sort(byName), [diff])
  const changedItems = useMemo(() => [...(diff?.changed_items ?? [])].sort(byName), [diff])
  const changedBlueprints = useMemo(
    () => [...(diff?.changed_blueprints ?? [])].sort((a, b) => a.product_name.localeCompare(b.product_name)),
    [diff],
  )
  const deltaRows = useMemo(() => {
    if (!diff) return []
    return Object.entries(diff.table_deltas)
      .map(([table, row]) => ({ table, old: row.old, neu: row.new, delta: row.new - row.old }))
      .sort((a, b) => a.table.localeCompare(b.table))
  }, [diff])

  const rerun = () => {
    setApplied(false)
    previewStart.mutate()
  }

  return (
    <Container size="lg" py="xl">
      <Group justify="space-between" mb="lg">
        <div>
          <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>Admin</Text>
          <Title order={1}>SDE-Update prüfen</Title>
        </div>
        <Button component={Link} to="/admin" variant="subtle" leftSection={<IconArrowLeft size={14} />}>
          Back
        </Button>
      </Group>

      <Stack gap="md">
        <Group>
          <Button size="xs" variant="default" onClick={rerun} loading={running} disabled={running}>
            Erneut prüfen
          </Button>
          <Button
            size="xs"
            onClick={() => apply.mutate()}
            loading={apply.isPending}
            disabled={!canApply || apply.isPending}
          >
            Änderungen übernehmen
          </Button>
        </Group>

        {running && (
          <Text size="sm" c="dimmed">
            {sdeJob.formatProgress(sdeJob.status?.progress, sdeJob.jobName)}
          </Text>
        )}

        {sdeJob.status?.status === 'failed' && (
          <Text size="sm" c="danger">{sdeJob.status.error || 'Preview failed.'}</Text>
        )}

        {diff && !running && (
          <>
            <Accordion multiple>
              <Accordion.Item value="new">
                <Accordion.Control>Neue Items ({newItems.length})</Accordion.Control>
                <Accordion.Panel><ItemList items={newItems} /></Accordion.Panel>
              </Accordion.Item>
              <Accordion.Item value="removed">
                <Accordion.Control>Entfernte Items ({removedItems.length})</Accordion.Control>
                <Accordion.Panel><ItemList items={removedItems} /></Accordion.Panel>
              </Accordion.Item>
              <Accordion.Item value="changed">
                <Accordion.Control>Geänderte Items ({changedItems.length})</Accordion.Control>
                <Accordion.Panel><ChangedItemList items={changedItems} /></Accordion.Panel>
              </Accordion.Item>
              <Accordion.Item value="blueprints">
                <Accordion.Control>Geänderte Blueprints ({changedBlueprints.length})</Accordion.Control>
                <Accordion.Panel>
                  {changedBlueprints.length === 0 ? (
                    <Text size="sm" c="dimmed">Keine.</Text>
                  ) : (
                    <Accordion>
                      {changedBlueprints.map((bp) => (
                        <Accordion.Item value={`bp-${bp.blueprint_type_id}`} key={bp.blueprint_type_id}>
                          <Accordion.Control>{bp.product_name}</Accordion.Control>
                          <Accordion.Panel><BlueprintPanel bp={bp} /></Accordion.Panel>
                        </Accordion.Item>
                      ))}
                    </Accordion>
                  )}
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>

            <div>
              <Title order={4} mb="xs">Übrige Tabellen</Title>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Tabelle</Table.Th>
                    <Table.Th>Alt</Table.Th>
                    <Table.Th>Neu</Table.Th>
                    <Table.Th>Delta</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {deltaRows.map((row) => (
                    <Table.Tr key={row.table}>
                      <Table.Td>{row.table}</Table.Td>
                      <Table.Td>{row.old.toLocaleString('en-US')}</Table.Td>
                      <Table.Td>{row.neu.toLocaleString('en-US')}</Table.Td>
                      <Table.Td>{row.delta.toLocaleString('en-US')}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
          </>
        )}
      </Stack>
    </Container>
  )
}
