import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Stack, Button, Group, Badge, MultiSelect, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { BuyListEntry } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { DetailRow } from '../../components/RowDetailDrawer'
import { HintCard } from '../../components/HintCard'
import { StalePlanAlert } from './StalePlanAlert'
import { PriceDataAlert } from './PriceDataAlert'
import { CapacityReasonBadge } from './ReactionCapacityPanel'
import { useAction } from '../../hooks/useAction'
import { isk, pct, qty } from '../../format'

const CATEGORY_UNKNOWN = 'no category'

const BUY_REASON_TEXT: Record<NonNullable<BuyListEntry['buy_reason']>, string> = {
  no_blueprint: 'No blueprint: it can only be bought.',
  capacity: 'Reaction capacity: slots or formulas were not enough to build it.', // refined by reasonText
  manual: 'Your manual Build/Buy override is set to Buy.',
  no_build_cost: 'Build cost unknown: a material has no price.',
  cheaper_to_buy: 'Buying is cheaper than building.',
  other: 'The recipe chain is deeper than the planner follows.',
}

const CAPACITY_REASON_TEXT: Record<string, string> = {
  capacity: 'Reaction capacity: slots or formulas were not enough to build it.',
  inputs: 'Reaction slots were free, but its reaction inputs are not on hand for this window.',
  formula: 'Formula purchase for the reaction plan.',
}

function reasonText(row: BuyListEntry): string {
  if (row.buy_reason === 'capacity' && row.capacity_reason) {
    return CAPACITY_REASON_TEXT[row.capacity_reason] ?? BUY_REASON_TEXT.capacity
  }
  return row.buy_reason ? BUY_REASON_TEXT[row.buy_reason] : ''
}

function signedIsk(value: number): string {
  return value > 0 ? `+${isk(value)}` : isk(value)
}

// "If built" section of the row detail drawer. Nothing for plans computed
// before the backend sent these fields (buy_reason missing).
function BuildOptionDetail({ row }: { row: BuyListEntry }) {
  if (!row.buy_reason) return null
  const build = row.unit_build_cost
  const savedPerUnit = build != null && row.unit_price != null ? row.unit_price - build : null
  const toBuy = row.to_buy ?? row.quantity
  return (
    <Stack gap={6}>
      <Text size="sm" fw={600}>If built</Text>
      <Text size="sm">{reasonText(row)}</Text>
      {row.buildable && (
        <>
          <DetailRow label="Build cost / unit" value={isk(build)} muted={build == null} />
          {savedPerUnit != null && (
            <>
              <DetailRow
                label="Building saves / unit"
                value={<Text span size="sm" ff="monospace" c={savedPerUnit > 0 ? 'accent' : 'danger'}>{signedIsk(savedPerUnit)}</Text>}
              />
              {toBuy > 0 && (
                <DetailRow
                  label={`Building saves (${qty(toBuy)} to buy)`}
                  value={<Text span size="sm" ff="monospace" c={savedPerUnit > 0 ? 'accent' : 'danger'}>{signedIsk(savedPerUnit * toBuy)}</Text>}
                />
              )}
            </>
          )}
          <DetailRow label="Margin if built and sold at home" value={pct(row.build_margin_home)} muted={row.build_margin_home == null} />
        </>
      )}
    </Stack>
  )
}

const ROW_DETAIL = { extra: (row: BuyListEntry) => <BuildOptionDetail row={row} /> }

export default function BuyList() {
  const { data: plan, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['production', 'plan'], queryFn: productionApi.plan })
  const buyList = plan?.buy_list ?? []

  const refreshPlan = useAction('Refresh Production', productionApi.refreshPlan, [
    ['production', 'plan'], ['production', 'stock-targets'], ['production', 'logistics'],
  ], { tier: 'live', effect: 'Recomputes the Buy/Build list with current Home prices (live ESI) and Jita prices (cached with live fallback).' })

  const categories = useMemo(
    () => [...new Set(buyList.map((e) => e.category ?? CATEGORY_UNKNOWN))].sort(), [buyList],
  )
  const [selCategories, setSelCategories] = useState<string[]>([])
  const filtered = useMemo(() => {
    if (selCategories.length === 0) return buyList
    return buyList.filter((e) => selCategories.includes(e.category ?? CATEGORY_UNKNOWN))
  }, [buyList, selCategories])

  const total = useMemo(() => filtered.reduce((sum, e) => sum + (e.total_price ?? 0), 0), [filtered])
  const unpricedCount = useMemo(
    () => filtered.filter((e) => e.total_price == null).length,
    [filtered],
  )

  // Best-hub mode (hub setting 'Best hub'): rows carry the hub their buy is priced at.
  const showHub = buyList.some((e) => e.hub_name)
  const columns = useMemo<ColumnDef<BuyListEntry, any>[]>(() => [
    {
      header: 'Item', accessorKey: 'type_name', size: 220, meta: { copyable: true },
      cell: (i) => {
        const row = i.row.original
        return (
          <Group gap="xs" wrap="nowrap">
            <Text>{row.type_name}</Text>
            <CapacityReasonBadge reason={row.capacity_reason} iskGivenUp={row.isk_given_up} />
          </Group>
        )
      },
    },
    { header: 'Category', accessorKey: 'category', size: 150, cell: (i) => i.getValue() ?? '–' },
    {
      header: 'Buy From', accessorKey: 'buy_from', size: 120,
      cell: (i) => {
        const v = i.getValue() as string | null
        return v ? <Badge color={v === 'C-J' ? 'accent' : 'info'} variant="light">{v}</Badge> : '–'
      },
    },
    ...(showHub ? [{ header: 'Best hub', accessorKey: 'hub_name', size: 100, cell: (i: any) => i.getValue() ?? '–' }] : []),
    { header: 'Quantity', accessorKey: 'quantity', size: 110, cell: (i) => qty(i.getValue()) },
    {
      header: 'On order', accessorKey: 'on_order', size: 100,
      cell: (i) => ((i.getValue() as number | undefined) ? qty(i.getValue()) : '–'),
    },
    {
      header: 'To buy', id: 'to_buy', size: 110, meta: { exportRole: 'qty' as const },
      accessorFn: (r) => r.to_buy ?? r.quantity,
      cell: (i) => (i.getValue() > 0
        ? qty(i.getValue())
        : <Badge size="sm" color="dimmed" variant="light">covered</Badge>),
    },
    {
      header: 'Special order', accessorKey: 'special_order_quantity', size: 130,
      cell: (i) => ((i.getValue() as number | undefined) ? qty(i.getValue()) : '–'),
    },
    {
      header: 'On Hand', accessorKey: 'on_hand_pct', size: 100,
      cell: (i) => {
        const v = i.getValue() as number
        const color = v >= 50 ? 'accent' : v > 0 ? 'warn' : 'gray'
        return <Badge color={color} variant="light">{v.toFixed(0)}%</Badge>
      },
    },
    { header: 'Unit Price', accessorKey: 'unit_price', size: 120, cell: (i) => isk(i.getValue()) },
    { header: 'Total Price', accessorKey: 'total_price', size: 140, cell: (i) => isk(i.getValue()) },
  ], [showHub])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (isError) return <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />
  if (!plan) {
    return (
      <Stack align="flex-start">
        <HintCard>No computation yet.</HintCard>
        <Tooltip label={refreshPlan.tooltip} disabled={!refreshPlan.tooltip} multiline w={280}>
          <Button leftSection={refreshPlan.tierIcon} onClick={() => refreshPlan.mutate()} loading={refreshPlan.isPending}>
            Compute Buy/Build List
          </Button>
        </Tooltip>
      </Stack>
    )
  }
  if (buyList.length === 0) {
    return (
      <Stack>
        <StalePlanAlert stale={plan.stale} />
        <PriceDataAlert flags={plan} />
        <HintCard>Nothing to buy - all stock targets are covered.</HintCard>
      </Stack>
    )
  }

  return (
    <Stack>
      <StalePlanAlert stale={plan.stale} />
      <PriceDataAlert flags={plan} />
      <Group justify="space-between" align="flex-end">
        <Group align="flex-end">
          <Card withBorder padding="sm" w={280}>
            <Text size="xs" c="dimmed" tt="uppercase">Total Buy List Value</Text>
            <Title order={3} c="accent">{isk(total)}</Title>
            {unpricedCount > 0 && (
              <Text size="xs" c="dimmed">+ {unpricedCount} row{unpricedCount === 1 ? '' : 's'} without a price</Text>
            )}
          </Card>
          <MultiSelect
            label="Category" data={categories} value={selCategories} onChange={setSelCategories}
            placeholder="All" clearable w={280}
          />
        </Group>
        <Tooltip label={refreshPlan.tooltip} disabled={!refreshPlan.tooltip} multiline w={280}>
          <Button variant="default" leftSection={refreshPlan.tierIcon} onClick={() => refreshPlan.mutate()} loading={refreshPlan.isPending}>
            Recompute
          </Button>
        </Tooltip>
      </Group>
      <Text size="xs" c="dimmed">
        {filtered.length} of {buyList.length} items. Open buy orders in the hub region (characters and corporations
        sharing Market Orders with Production) count as On order; Total Price covers only what is left to buy.
      </Text>
      {filtered.length === 0 ? (
        <HintCard>No items in the selected categories.</HintCard>
      ) : (
        <DataTable tableId="production-buy-list" rowDetail={ROW_DETAIL} data={filtered} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt} />
      )}
    </Stack>
  )
}
