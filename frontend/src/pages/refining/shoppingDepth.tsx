import { Alert, Badge, Group, Text, Title, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import type { ShoppingByproduct } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { isk, iskUnit, qty } from '../../format'

// Shared by both mineral shopping lists (Ore & Minerals, Module Reprocessing):
// the optimizer only buys what sellers list within the price tolerance.

export function SourceBadge({ source, depthChecked }: { source: string | null; depthChecked: boolean }) {
  return (
    <Group gap={4} wrap="nowrap">
      <Badge size="sm" variant="light" color={source === 'Home' ? 'accent' : 'gray'}>{source ?? '–'}</Badge>
      {!depthChecked && (
        <Tooltip label="Home-market quote: no listed volume available, quantity not checked" multiline w={240}>
          <Badge size="sm" variant="outline" color="warn">qty unchecked</Badge>
        </Tooltip>
      )}
    </Group>
  )
}

export function ShortCell({ value }: { value: number }) {
  return value > 0 ? <Text size="sm" c="danger" fw={600}>{qty(value)}</Text> : <>–</>
}

export function ShortfallAlert({ show, tolerance }: { show: boolean; tolerance: number | undefined }) {
  if (!show) return null
  const pct = tolerance != null ? `${(tolerance * 100).toFixed(1).replace(/\.0$/, '')}%` : 'the tolerance'
  return (
    <Alert color="danger" variant="light" title="Not everything is available at an acceptable price">
      Sellers do not list enough within {pct} of the reference price, even after switching to other sources.
      See the Short column in the Coverage Check.
    </Alert>
  )
}

export function ApproximateAlert({ show }: { show: boolean }) {
  if (!show) return null
  return (
    <Alert color="warn" variant="light" title="Approximate plan">
      The whole-unit optimization did not finish in time, so this plan is the continuous optimum rounded up. It covers
      the list but can cost slightly more than the best whole-unit plan.
    </Alert>
  )
}

export const DEPTH_HINT =
  'Quantities are capped at what sellers list within the price tolerance (Settings); deeper orders cost more and ' +
  'are used only while they still beat every other source.'

const BYPRODUCT_COLUMNS: ColumnDef<ShoppingByproduct, any>[] = [
  { header: 'Material', accessorKey: 'name', size: 180 },
  { header: 'Quantity', accessorKey: 'quantity', size: 130, cell: (i) => qty(i.getValue()) },
  { header: 'Value / Unit', accessorKey: 'unit_value', size: 120, cell: (i) => iskUnit(i.getValue()) },
  { header: 'Total Value', accessorKey: 'total_value', size: 140, cell: (i) => isk(i.getValue()) },
  {
    header: 'From', accessorKey: 'sources', size: 320, enableSorting: false,
    cell: (i) => <Text size="sm" truncate>{(i.getValue() as string[]).join(', ')}</Text>,
  },
]

// Outputs of the plan's reprocessing that are not on the list (e.g. Atmospheric
// Gases from Zeolites). Shared by both mineral shopping lists.
export function ByproductsSection({ byproducts, credited }: { byproducts: ShoppingByproduct[]; credited: boolean }) {
  if (byproducts.length === 0) return null
  return (
    <>
      <Title order={6} c="dimmed" tt="uppercase" mt="md">By-products (not on the list)</Title>
      <Text size="xs" c="dimmed">
        Valued at the home-market sell quote after the structure sell haircut; market depth is not checked.{' '}
        {credited
          ? 'Their value is credited in the optimizer (Settings), so sources with valuable by-products cost less.'
          : 'Their value is not credited in the optimizer; turn on "Credit by-product value" in Settings to do so.'}
      </Text>
      <DataTable data={byproducts} columns={BYPRODUCT_COLUMNS} maxHeight={260} getRowId={(r) => String(r.type_id)} />
    </>
  )
}

// One line under the Total Cost card: what the plan costs once the by-products are sold.
export function NetCostLine({ byproductValue, netCost }: { byproductValue: number; netCost: number }) {
  if (byproductValue <= 0) return null
  return <Text size="xs" c="dimmed">net {isk(netCost)} after selling by-products ({isk(byproductValue)})</Text>
}
