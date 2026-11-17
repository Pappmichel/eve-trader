import { useMemo } from 'react'
import { Alert, Button, Stack, Text, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { ProductionUnlistedStockRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { ItemWithNeeds } from '../../components/NeedBadges'
import { HintCard } from '../../components/HintCard'
import { JobProgress } from '../../components/JobProgress'
import { ActionTierIcon, TIER_COPY } from '../../components/ActionTierIcon'
import { useBackgroundJob, useBackgroundJobStart } from '../../hooks/useBackgroundJob'
import { pct, qty } from '../../format'

const UNLISTED_LABELS = { unlisted_stock: 'Check Structure Stock Without Order' }

export default function UnlistedStock() {
  // Background job: corporation assets/orders are read live from ESI, which
  // can take minutes. The last run's rows come back with the status, so a
  // reload shows them again.
  const job = useBackgroundJob({
    queryKey: ['production', 'unlisted-stock', 'status'],
    fetchStatus: productionApi.checkUnlistedStockStatus,
    resultKeys: [],
    labels: UNLISTED_LABELS,
    defaultLabel: 'Check Structure Stock Without Order',
    pollIntervalMs: 1500,
  })
  const start = useBackgroundJobStart(job, () => productionApi.checkUnlistedStock())
  const running = job.runningStatus || start.isPending
  const rows = job.status?.result?.rows
  const data = job.status?.status === 'succeeded' && Array.isArray(rows)
    ? (rows as ProductionUnlistedStockRow[]) : undefined

  const columns = useMemo<ColumnDef<ProductionUnlistedStockRow, any>[]>(() => [
    {
      header: 'Item', accessorKey: 'type_name', size: 400, meta: { copyable: true },
      cell: (i) => (
        <ItemWithNeeds name={i.getValue()} shortfall={i.row.original.doctrine_shortfall}
          doctrines={i.row.original.doctrine_names} productionNeeded={i.row.original.production_needed} />
      ),
    },
    { header: 'Stock in C-J Hangar', accessorKey: 'stock_quantity', size: 170, cell: (i) => qty(i.getValue()) },
    { header: 'Listed Qty (C-J)', accessorKey: 'sell_volume', size: 130, cell: (i) => qty(i.getValue()) },
    { header: 'Margin (Home)', accessorKey: 'margin', size: 110, cell: (i) => pct(i.getValue()) },
  ], [])

  return (
    <Stack>
      <HintCard>
        Live-checks stock targets at the configured location (Settings) that have no sell order yet - personal
        <b>and</b> corp orders/hangars. Corp orders need the Accountant or Trader role on at least one registered
        character. Only targets with a <b>Home</b> or <b>Jita Market Target</b> are considered. A <b>Doctrine needs</b>
        badge marks items the doctrine stockpile is currently short of, a <b>Production needs</b> badge items on the
        last Build List's buy list. The check runs in the background; you can leave the page and come back
        to the last result.
      </HintCard>

      <Tooltip
        label={`Compares your ESI asset/corp hangar stock live against currently open sell orders - runs as a background job. ${TIER_COPY.live}`}
        multiline w={280}
      >
        <Button w={280} rightSection={<ActionTierIcon tier="live" />} variant={running ? 'light' : undefined}
          onClick={() => start.mutate()} disabled={running}>
          Check Structure Stock Without Order
        </Button>
      </Tooltip>

      {running && (
        <JobProgress label={job.formatProgress(job.status?.progress, job.jobName)} progress={job.status?.progress} />
      )}

      {!running && job.status?.status === 'failed' && (
        <Alert color="red" title="Check failed">{job.status.error || 'The background job failed.'}</Alert>
      )}

      {!running && data && data.length === 0 && (
        <HintCard>All listing-target stock in the C-J hangar is listed.</HintCard>
      )}

      {!running && data && data.length > 0 && (
        <>
          <Text size="sm" c="dimmed">{data.length} listing-target stock items in the C-J hangar without a sell order</Text>
          <DataTable getRowId={(r) => String(r.type_id)} tableId="production-unlisted-stock" rowDetail data={data} columns={columns} maxHeight={560} />
        </>
      )}
    </Stack>
  )
}
