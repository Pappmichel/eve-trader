import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Button, Group, Stack, Text, Tooltip } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { ApiError, productionApi } from '../../../api/client'
import type { RealizedProductRow, RealizedSaleRow } from '../../../api/types'
import { DataTable } from '../../../components/DataTable'
import { HintCard } from '../../../components/HintCard'
import { DetailRow, RowDetailDrawer } from '../../../components/RowDetailDrawer'
import { dateTime, isk, pct, qty } from '../../../format'
import { CostChainTree } from './CostChainTree'
import { CoverageBadge, FlagMarkers } from './flags'
import { locationLabel } from './labels'

function when(iso: string | null): string {
  return iso ? dateTime(iso) : '–'
}

function SaleChain({ saleRef, locationNames }: {
  saleRef: string
  locationNames: Record<string, string | null> | undefined
}) {
  const query = useQuery({
    queryKey: ['production', 'realized', 'sale-chain', saleRef],
    queryFn: () => productionApi.realizedSaleChain(saleRef),
    retry: false,
  })
  if (query.isLoading) return <Text size="sm" c="dimmed">Loading cost chain…</Text>
  if (query.isError) {
    const message = query.error instanceof ApiError ? query.error.message : 'Failed to load the cost chain.'
    return <HintCard>{message}</HintCard>
  }
  if (!query.data) return null
  return (
    <Stack gap="xs">
      <Text size="sm">Sale cost {isk(query.data.sale.cost)}</Text>
      <CostChainTree node={query.data.tree} locationNames={locationNames} />
    </Stack>
  )
}

function ProductDrawer({ typeId, periodQuery, onClose, locationNames }: {
  typeId: number
  periodQuery: string
  onClose: () => void
  locationNames: Record<string, string | null> | undefined
}) {
  const [openSale, setOpenSale] = useState<string | null>(null)
  const query = useQuery({
    queryKey: ['production', 'realized', 'product', typeId, periodQuery],
    queryFn: () => productionApi.realizedProduct(typeId, periodQuery),
    retry: false,
  })
  const product = query.data?.product
  const sales = query.data?.sales ?? []
  const shownSale = openSale ?? sales[0]?.sale_ref ?? null

  return (
    <RowDetailDrawer
      opened
      size="xl"
      onClose={onClose}
      title={product?.type_name || `Type ${typeId}`}
    >
      {query.isError && (
        <HintCard>{query.error instanceof ApiError ? query.error.message : 'Failed to load this product.'}</HintCard>
      )}
      {query.data?.needs_rebuild && (
        <HintCard>Recompute to build realized profit from the archive.</HintCard>
      )}
      {product && (
        <Stack gap={4}>
          <DetailRow label="Units sold" value={qty(product.units_sold)} />
          <DetailRow label="Revenue" value={isk(product.revenue)} />
          <DetailRow label="Profit" value={isk(product.profit)} />
          <DetailRow label="Coverage" value={product.coverage == null ? '–' : pct(product.coverage)} muted={product.coverage == null} />
          <FlagMarkers flags={product.flags} />
        </Stack>
      )}
      <Text size="sm" fw={600}>Sales</Text>
      {sales.length === 0 && !query.isLoading && <Text size="sm" c="dimmed">No sales in this period.</Text>}
      {sales.map((sale) => (
        <SaleBlock
          key={sale.sale_ref}
          sale={sale}
          open={shownSale === sale.sale_ref}
          onToggle={() => setOpenSale(sale.sale_ref)}
          locationNames={locationNames}
        />
      ))}
    </RowDetailDrawer>
  )
}

function SaleBlock({ sale, open, onToggle, locationNames }: {
  sale: RealizedSaleRow
  open: boolean
  onToggle: () => void
  locationNames: Record<string, string | null> | undefined
}) {
  return (
    <Stack gap={4}>
      <Group justify="space-between" wrap="nowrap">
        <Text size="sm">{when(sale.sale_date)} · {qty(sale.quantity)} · {isk(sale.net_price)} net · {isk(sale.profit)}</Text>
        <Button size="compact-xs" variant="subtle" onClick={onToggle}>
          {open ? 'Hide cost chain' : 'Cost chain'}
        </Button>
      </Group>
      <Text size="xs" c="dimmed">{locationLabel(sale.location_id, locationNames)}</Text>
      {sale.sales_tax == null && <Badge size="xs" color="warn" variant="light">tax missing</Badge>}
      <FlagMarkers flags={sale.flags} />
      {open && <SaleChain saleRef={sale.sale_ref} locationNames={locationNames} />}
    </Stack>
  )
}

export function ProductsTab({ rows, needsRebuild, hint, periodQuery, typeId, locationNames, isLoading, isError, onRetry, updatedAt }: {
  rows: RealizedProductRow[]
  needsRebuild: boolean
  hint: string
  periodQuery: string
  typeId: number | null
  locationNames: Record<string, string | null> | undefined
  isLoading: boolean
  isError: boolean
  onRetry: () => void
  updatedAt?: number
}) {
  const [selected, setSelected] = useState<number | null>(null)
  const openId = selected
  const columns: ColumnDef<RealizedProductRow, any>[] = [
    { header: 'Name', accessorKey: 'type_name', size: 220, cell: (info) => info.getValue() || `Type ${info.row.original.type_id}` },
    { header: 'Units sold', accessorKey: 'units_sold', size: 110, cell: (info) => qty(info.getValue()) },
    { header: 'Revenue', accessorKey: 'revenue', size: 130, cell: (info) => isk(info.getValue()) },
    { header: 'Cost/unit', accessorKey: 'cost_per_unit', size: 120, cell: (info) => isk(info.getValue()) },
    { header: 'Net sell/unit', accessorKey: 'net_sell_per_unit', size: 130, cell: (info) => isk(info.getValue()) },
    { header: 'Profit', accessorKey: 'profit', size: 130, cell: (info) => isk(info.getValue()) },
    { header: 'Margin', accessorKey: 'margin', size: 100, cell: (info) => info.getValue() == null ? '–' : pct(info.getValue()) },
    {
      header: 'Coverage', accessorKey: 'coverage', size: 110,
      cell: (info) => <CoverageBadge coverage={info.getValue()} />,
    },
    {
      header: '', id: 'warnings', size: 48, enableSorting: false,
      accessorFn: (row) => row.warnings.join(', '),
      cell: (info) => {
        const warnings = info.row.original.warnings
        if (warnings.length === 0) return null
        return (
          <Tooltip label={warnings.join('\n')} multiline w={280}>
            <IconAlertTriangle size={16} color="var(--mantine-color-warn-5)" aria-label={warnings.join(', ')} />
          </Tooltip>
        )
      },
    },
    {
      header: 'Flags', id: 'flags', size: 220,
      accessorFn: (row) => JSON.stringify(row.flags),
      cell: (info) => <FlagMarkers flags={info.row.original.flags} showCoverage={false} />,
    },
  ]

  if (needsRebuild) return <HintCard>{hint}</HintCard>

  return (
    <>
      <DataTable
        tableId="production-realized-products"
        data={typeId == null ? rows : rows.filter((row) => row.type_id === typeId)}
        columns={columns}
        maxHeight={560}
        isLoading={isLoading}
        isError={isError}
        onRetry={onRetry}
        dataUpdatedAt={updatedAt}
        getRowId={(row) => String(row.type_id)}
        onRowClick={(row) => setSelected(row.type_id)}
        activeRowId={openId == null ? undefined : String(openId)}
        rowDetail={false}
      />
      {openId != null && (
        <ProductDrawer
          typeId={openId}
          periodQuery={periodQuery}
          locationNames={locationNames}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  )
}
