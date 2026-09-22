import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Accordion, Button, Container, Group, Stack, Table, Text, Title,
} from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import type { ColumnDef } from '@tanstack/react-table'

import { adminApi } from '../../api/client'
import type {
  SdeChangedBlueprint, SdeChangedItem, SdeDiff, SdeDiffItem,
} from '../../api/types'
import { DataTable } from '../../components/DataTable'
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
    && typeof v.other_tables === 'object'
    && v.other_tables !== null
    && !Array.isArray(v.other_tables)
}

function rowKey(row: { key?: string; type_id?: number }): string {
  return row.key ?? String(row.type_id)
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '–'
  return String(value)
}

const ITEM_COLUMNS: ColumnDef<SdeDiffItem, unknown>[] = [
  { header: 'Name', accessorKey: 'name', size: 280 },
  { header: 'ID', id: 'id', size: 140, accessorFn: (row) => row.key ?? row.type_id },
]

const CHANGED_ITEM_COLUMNS: ColumnDef<SdeChangedItem, unknown>[] = [
  { header: 'Name', accessorKey: 'name', size: 240 },
  { header: 'ID', id: 'id', size: 120, accessorFn: (row) => row.key ?? row.type_id },
  {
    header: 'Changes',
    id: 'changes',
    size: 420,
    enableSorting: false,
    accessorFn: (row) => Object.entries(row.changes)
      .map(([field, pair]) => `${field}: ${formatValue(pair[0])} → ${formatValue(pair[1])}`)
      .join('; '),
  },
]

function ItemList({ items, tableId }: { items: SdeDiffItem[]; tableId: string }) {
  if (items.length === 0) return <Text size="sm" c="dimmed">None.</Text>
  return (
    <DataTable
      data={items}
      columns={ITEM_COLUMNS}
      tableId={tableId}
      exportFilename={tableId}
      getRowId={(row) => rowKey(row)}
      maxHeight={360}
    />
  )
}

function ChangedItemList({ items, tableId }: { items: SdeChangedItem[]; tableId: string }) {
  if (items.length === 0) return <Text size="sm" c="dimmed">None.</Text>
  return (
    <DataTable
      data={items}
      columns={CHANGED_ITEM_COLUMNS}
      tableId={tableId}
      exportFilename={tableId}
      getRowId={(row) => rowKey(row)}
      maxHeight={360}
    />
  )
}

function BlueprintPanel({ bp }: { bp: SdeChangedBlueprint }) {
  return (
    <Stack gap="sm">
      {bp.materials.length > 0 && (
        <div>
          <Text size="sm" fw={600} mb={4}>Materials</Text>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Material</Table.Th>
                <Table.Th>Old</Table.Th>
                <Table.Th>New</Table.Th>
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
          <Text size="sm" fw={600} mb={4}>Products</Text>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Field</Table.Th>
                <Table.Th>Old</Table.Th>
                <Table.Th>New</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              <Table.Tr>
                <Table.Td>Quantity</Table.Td>
                <Table.Td>{bp.products.old_qty}</Table.Td>
                <Table.Td>{bp.products.new_qty}</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        </div>
      )}
      {bp.time && (
        <div>
          <Text size="sm" fw={600} mb={4}>Build Time</Text>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Field</Table.Th>
                <Table.Th>Old</Table.Th>
                <Table.Th>New</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              <Table.Tr>
                <Table.Td>Time</Table.Td>
                <Table.Td>{formatValue(bp.time.old)}</Table.Td>
                <Table.Td>{formatValue(bp.time.new)}</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        </div>
      )}
      {bp.invention_probability && (
        <div>
          <Text size="sm" fw={600} mb={4}>Invention Probability</Text>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Field</Table.Th>
                <Table.Th>Old</Table.Th>
                <Table.Th>New</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              <Table.Tr>
                <Table.Td>Probability</Table.Td>
                <Table.Td>{formatValue(bp.invention_probability.old)}</Table.Td>
                <Table.Td>{formatValue(bp.invention_probability.new)}</Table.Td>
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
  const apply = useAction('Apply Changes', async () => {
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
  const otherTables = useMemo(() => {
    if (!diff) return []
    return Object.entries(diff.other_tables)
      .map(([table, rows]) => ({
        table,
        newRows: [...rows.new].sort(byName),
        removedRows: [...rows.removed].sort(byName),
        changedRows: [...rows.changed].sort(byName),
      }))
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
          <Title order={1}>Preview SDE Update</Title>
        </div>
        <Button component={Link} to="/admin" variant="subtle" leftSection={<IconArrowLeft size={14} />}>
          Back
        </Button>
      </Group>

      <Stack gap="md">
        <Group>
          <Button size="xs" variant="default" onClick={rerun} loading={running} disabled={running}>
            Re-check
          </Button>
          <Button
            size="xs"
            onClick={() => apply.mutate()}
            loading={apply.isPending}
            disabled={!canApply || apply.isPending}
          >
            Apply Changes
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
                <Accordion.Control>New Items ({newItems.length})</Accordion.Control>
                <Accordion.Panel><ItemList items={newItems} tableId="sde-preview-new-items" /></Accordion.Panel>
              </Accordion.Item>
              <Accordion.Item value="removed">
                <Accordion.Control>Removed Items ({removedItems.length})</Accordion.Control>
                <Accordion.Panel><ItemList items={removedItems} tableId="sde-preview-removed-items" /></Accordion.Panel>
              </Accordion.Item>
              <Accordion.Item value="changed">
                <Accordion.Control>Changed Items ({changedItems.length})</Accordion.Control>
                <Accordion.Panel>
                  <ChangedItemList items={changedItems} tableId="sde-preview-changed-items" />
                </Accordion.Panel>
              </Accordion.Item>
              <Accordion.Item value="blueprints">
                <Accordion.Control>Changed Blueprints ({changedBlueprints.length})</Accordion.Control>
                <Accordion.Panel>
                  {changedBlueprints.length === 0 ? (
                    <Text size="sm" c="dimmed">None.</Text>
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
              <Title order={4} mb="xs">Other Tables</Title>
              <Accordion multiple>
                {otherTables.map((entry) => (
                  <Accordion.Item value={entry.table} key={entry.table}>
                    <Accordion.Control>
                      {entry.table} ({entry.newRows.length} new, {entry.removedRows.length} removed, {entry.changedRows.length} changed)
                    </Accordion.Control>
                    <Accordion.Panel>
                      <Stack gap="sm">
                        <div>
                          <Text size="sm" fw={600} mb={4}>New ({entry.newRows.length})</Text>
                          <ItemList items={entry.newRows} tableId={`sde-preview-${entry.table}-new`} />
                        </div>
                        <div>
                          <Text size="sm" fw={600} mb={4}>Removed ({entry.removedRows.length})</Text>
                          <ItemList items={entry.removedRows} tableId={`sde-preview-${entry.table}-removed`} />
                        </div>
                        <div>
                          <Text size="sm" fw={600} mb={4}>Changed ({entry.changedRows.length})</Text>
                          <ChangedItemList items={entry.changedRows} tableId={`sde-preview-${entry.table}-changed`} />
                        </div>
                      </Stack>
                    </Accordion.Panel>
                  </Accordion.Item>
                ))}
              </Accordion>
            </div>
          </>
        )}
      </Stack>
    </Container>
  )
}
