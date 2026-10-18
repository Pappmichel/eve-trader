import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Container, Title, Text, SimpleGrid, Card, Group, Stack, Button, Loader, Center, SegmentedControl,
  NumberInput, ActionIcon, Alert, Divider, Badge, Anchor,
} from '@mantine/core'
import { IconArrowLeft, IconTrash, IconCheck } from '@tabler/icons-react'
import { modals } from '@mantine/modals'
import { Link } from 'react-router-dom'
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid, Legend } from 'recharts'
import type { ColumnDef } from '@tanstack/react-table'

import { gateApi, portfolioApi } from '../api/client'
import { DataTable } from '../components/DataTable'
import { SearchableSelect } from '../components/SearchableSelect'
import { useAction } from '../hooks/useAction'
import { useItemNameOptions } from '../hooks/useStaticOptions'
import { isk, qty, pct, dateTime } from '../format'
import { COLORS } from '../theme'
import { toolLabel } from '../esiRegistry'
import type { ManualItemPriceRow, PortfolioSnapshotRow, RealizedToolCard, WealthChange } from '../api/types'
import { CoverageBadge } from './production/realized/flags'

const PERIOD_KEY = 'portfolio-realized-period'
const PERIOD_OPTIONS = [
  { label: '7d', value: '7' },
  { label: '30d', value: '30' },
  { label: '90d', value: '90' },
  { label: 'Since T0', value: 'since_t0' },
]

function readPeriod(): string {
  try {
    const value = localStorage.getItem(PERIOD_KEY)
    if (value === '7' || value === '30' || value === '90' || value === 'since_t0') return value
  } catch {
    // Private mode can throw. The page still opens on the default period.
  }
  return '30'
}

function writePeriod(value: string) {
  try {
    localStorage.setItem(PERIOD_KEY, value)
  } catch {
    // Remembering the period is optional.
  }
}

function WealthChanges({ change }: { change: WealthChange | undefined }) {
  const rows: { label: string; point: WealthChange['d1'] }[] = [
    { label: '1 day', point: change?.d1 ?? null },
    { label: '7 days', point: change?.d7 ?? null },
    { label: '30 days', point: change?.d30 ?? null },
  ]
  return (
    <Stack gap={2} mt="xs">
      {rows.map((row) => (
        <Text key={row.label} size="xs" c="dimmed">
          {row.label}: {row.point ? `${isk(row.point.isk)} (${pct(row.point.pct)})` : '–'}
        </Text>
      ))}
    </Stack>
  )
}

function RealizedProfitCard({ row }: { row: RealizedToolCard }) {
  return (
    <Card withBorder padding="lg" radius="md" className="et-panel">
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{toolLabel(row.tool)}</Title>
      <Text size="xl" fw={700}>{isk(row.profit)}</Text>
      <Text size="xs" c="dimmed" mt={4}>Revenue {isk(row.revenue)}</Text>
      <Text size="xs" c="dimmed">Margin {pct(row.margin)} · {qty(row.sales_count)} sales</Text>
      {!row.empty && (
        <Group gap={6} mt={4}>
          <CoverageBadge coverage={row.coverage} />
          {row.estimated && <Badge size="xs" color="warn" variant="light">estimated</Badge>}
        </Group>
      )}
      <Anchor component={Link} to={row.link} size="xs">Open report</Anchor>
    </Card>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card withBorder padding="lg" radius="md" className="et-panel">
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{label}</Title>
      <Text size="xl" fw={700}>{value}</Text>
      {hint && <Text size="xs" c="dimmed" mt={4}>{hint}</Text>}
    </Card>
  )
}

const RANGE_OPTIONS = [
  { label: '7d', value: '7' },
  { label: '30d', value: '30' },
  { label: '90d', value: '90' },
  { label: 'All', value: 'all' },
]

function tooltipStyle() {
  return { background: COLORS.surface2, border: `1px solid ${COLORS.border}` }
}

function HistoryChart({ title, hint, rows, lines }: {
  title: string
  hint?: string
  rows: PortfolioSnapshotRow[]
  lines: { dataKey: keyof PortfolioSnapshotRow; name: string; color: string; formatter: (v: number) => string }[]
}) {
  // Fewer than 2 days of history has no meaningful trend to chart -
  // mirrors trading_daily_profit_volatility's own "None below 2 days of
  // data" precedent rather than rendering an empty/broken chart.
  if (rows.length < 2) {
    return (
      <Card withBorder padding="lg" radius="md" className="et-panel">
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">{title}</Title>
        <Text size="sm" c="dimmed">History builds up from here - check back tomorrow.</Text>
      </Card>
    )
  }
  return (
    <Card withBorder padding="lg" radius="md" className="et-panel">
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{title}</Title>
      {hint && <Text size="xs" c="dimmed" mb="sm">{hint}</Text>}
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={rows}>
          <CartesianGrid stroke={COLORS.border} />
          <XAxis dataKey="snapshot_date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
          <YAxis stroke={COLORS.textDim} tick={{ fontSize: 11 }} width={90}
            tickFormatter={(v: number) => lines[0].formatter(v)} />
          <Tooltip contentStyle={tooltipStyle()} formatter={(v, name) => {
            const line = lines.find((l) => l.name === name)
            return [line && typeof v === 'number' ? line.formatter(v) : v, name]
          }} />
          {lines.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
          {lines.map((line) => (
            <Line key={String(line.dataKey)} type="monotone" dataKey={line.dataKey} name={line.name}
              stroke={line.color} dot={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Card>
  )
}

const MANUAL_PRICES_KEY = [['portfolio', 'manual-prices']]
const WEALTH_KEY = [['portfolio', 'wealth']]

// Local draft state, checkmark appears once it differs from the saved
// value, click to save - same pattern as production/Blueprints.tsx's own
// EditableCopyCostCell.
function EditablePriceCell({ value, ariaLabel, isPending, onSave }: {
  value: number
  ariaLabel: string
  isPending: boolean
  onSave: (value: number) => void
}) {
  const [draft, setDraft] = useState(value)
  const dirty = draft !== value
  return (
    <Group gap={4} wrap="nowrap">
      <NumberInput value={draft} onChange={(v) => setDraft(v === '' ? 0 : Number(v))}
        min={0} size="xs" w={130} aria-label={ariaLabel} />
      {dirty && (
        <ActionIcon size="sm" variant="filled" color="accent" aria-label={`Save ${ariaLabel}`}
          onClick={() => onSave(draft)} loading={isPending}>
          <IconCheck size={14} />
        </ActionIcon>
      )}
    </Group>
  )
}

function ManualPricesSection() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['portfolio', 'manual-prices'], queryFn: portfolioApi.manualPrices,
  })
  const setPrice = useAction(
    'Set Manual Price',
    (args: { itemName: string; price: number }) => portfolioApi.setManualPrice(args.itemName, args.price),
    [...MANUAL_PRICES_KEY, ...WEALTH_KEY],
  )
  const removePrice = useAction('Remove Manual Price', portfolioApi.removeManualPrice, [...MANUAL_PRICES_KEY, ...WEALTH_KEY])
  // Same one-shared-mutation-instance caveat as production/Blueprints.tsx's
  // own pendingTypeId/pendingEditTypeId (GitHub issue #59).
  const [pendingRemoveTypeId, setPendingRemoveTypeId] = useState<number | null>(null)
  const [pendingEditTypeId, setPendingEditTypeId] = useState<number | null>(null)

  const { data: itemNameOptions } = useItemNameOptions()
  const itemOptions = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [itemId, setItemId] = useState<string | null>(null)
  const [priceInput, setPriceInput] = useState<number | ''>('')

  const columns = useMemo<ColumnDef<ManualItemPriceRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 260, meta: { copyable: true } },
    {
      header: 'Price (ISK)', accessorKey: 'price', size: 170,
      cell: (i) => (
        <EditablePriceCell value={i.getValue()} ariaLabel={`Price for ${i.row.original.type_name}`}
          isPending={setPrice.isPending && pendingEditTypeId === i.row.original.type_id}
          onSave={(v) => {
            setPendingEditTypeId(i.row.original.type_id)
            setPrice.mutate({ itemName: i.row.original.type_name, price: v })
          }} />
      ),
    },
    { header: 'Updated', accessorKey: 'updated_at', size: 170, cell: (i) => dateTime(i.getValue()) },
    {
      header: '', id: 'actions', size: 60, enableSorting: false,
      cell: (i) => (
        <ActionIcon size="sm" variant="subtle" color="danger"
          onClick={() => modals.openConfirmModal({
            title: 'Remove manual price',
            children: <Text size="sm">Remove the manual price for {i.row.original.type_name}?</Text>,
            labels: { confirm: 'Remove', cancel: 'Cancel' },
            confirmProps: { color: 'danger' },
            onConfirm: () => { setPendingRemoveTypeId(i.row.original.type_id); removePrice.mutate(i.row.original.type_id) },
          })}
          loading={removePrice.isPending && pendingRemoveTypeId === i.row.original.type_id}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [setPrice, pendingEditTypeId, removePrice, pendingRemoveTypeId])

  return (
    <div>
      <Title order={5} mb="xs">Manual Prices</Title>
      <Text size="sm" c="dimmed" mb="sm">
        Used only to value items Goonmetrics has no quote for in the Total Wealth calculation above - Production's
        and Trading's own pricing are untouched.
      </Text>

      <Card withBorder mb="sm">
        <Group grow align="flex-end">
          <SearchableSelect label="Item name" placeholder="Search item…" data={itemOptions} value={itemId} onChange={setItemId} />
          <NumberInput label="Price (ISK)" value={priceInput} onChange={(v) => setPriceInput(v === '' ? '' : Number(v))} min={0} />
          <Button
            disabled={!itemId || priceInput === ''}
            loading={setPrice.isPending}
            onClick={() => setPrice.mutate(
              { itemName: itemOptions.find((o) => o.value === itemId)?.label ?? '', price: Number(priceInput) },
              { onSuccess: () => { setItemId(null); setPriceInput('') } },
            )}
          >
            Add
          </Button>
        </Group>
      </Card>

      {isLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={300} />
      ) : isError ? (
        <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={300} />
      ) : !data || data.length === 0 ? (
        <Text c="dimmed" size="sm">None registered yet.</Text>
      ) : (
        <DataTable data={data} columns={columns} tableId="portfolio-manual-prices"
          exportFilename="portfolio-manual-prices" getRowId={(r) => String(r.type_id)} maxHeight={300}
          dataUpdatedAt={dataUpdatedAt} />
      )}
    </div>
  )
}

function TotalWealthSection() {
  const { data } = useQuery({ queryKey: ['portfolio', 'wealth'], queryFn: portfolioApi.wealth })
  if (!data) return null

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }} spacing="md">
        <Stat label="Assets" value={isk(data.wealth_assets_value)} />
        <Stat label="Blueprints" value={isk(data.wealth_blueprints_value)}
          hint="One market quote per BPO, regardless of its own ME/TE - a BPC has no separate quote of its own, so it counts here only once you set a manual price for it" />
        <Stat label="Wallet" value={isk(data.wealth_wallet_balance)} />
      </SimpleGrid>

      {data.wealth_unpriced_items > 0 && (
        <Text size="xs" c="dimmed">
          {qty(data.wealth_unpriced_items)} owned item type(s) have no price and are excluded from the total above -
          add a manual price below to include them.
        </Text>
      )}

      {data.characters_missing_wallet_scope.length > 0 && (
        <Alert color="warn" variant="light" title="Wallet balance not included for some characters">
          {data.characters_missing_wallet_scope.map((c) => c.character_name).join(', ')}{' '}
          {data.characters_missing_wallet_scope.length === 1 ? 'shares' : 'share'} Assets/Blueprints with Portfolio
          but {data.characters_missing_wallet_scope.length === 1 ? 'has' : 'have'} no wallet scope
          shared - reauthorize on the{' '}
          <Text component={Link} to="/character-management/characters" span c="accent" td="underline">Characters page</Text>
          {' '}to include their ISK balance.
        </Alert>
      )}
    </Stack>
  )
}

export default function Portfolio() {
  const gate = useQuery({ queryKey: ['gate', 'status'], queryFn: gateApi.status })
  const showTrading = Array.isArray(gate.data?.tools) && gate.data.tools.includes('trading')
  const overview = useQuery({ queryKey: ['portfolio', 'overview'], queryFn: portfolioApi.overview })
  const wealth = useQuery({ queryKey: ['portfolio', 'wealth'], queryFn: portfolioApi.wealth })
  const [period, setPeriod] = useState(readPeriod)
  const cards = useQuery({
    queryKey: ['portfolio', 'realized-cards', period],
    queryFn: () => portfolioApi.realizedCards(period),
    retry: false,
  })
  const [range, setRange] = useState('30')
  const { data: history } = useQuery({
    queryKey: ['portfolio', 'history', range],
    queryFn: () => portfolioApi.history(range === 'all' ? undefined : Number(range)),
  })

  return (
    <Container size="md" py="xl">
      <Group justify="space-between" mb="lg">
        <Title order={1}>Portfolio Overview</Title>
        <Button component={Link} to="/" variant="subtle" leftSection={<IconArrowLeft size={14} />}>Back</Button>
      </Group>

      <Stack gap="lg">
        {wealth.isLoading && <Center h={80}><Loader color="accent" /></Center>}
        {wealth.data && (
          <Card withBorder padding="lg" radius="md" className="et-panel">
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Total Wealth</Title>
            <Text size="xl" fw={700}>{isk(wealth.data.total_wealth)}</Text>
            <WealthChanges change={overview.data?.wealth_change} />
          </Card>
        )}
        <TotalWealthSection />

        <Group justify="space-between" align="center">
          <Title order={4}>Realized profit</Title>
          <SegmentedControl size="xs" value={period} data={PERIOD_OPTIONS} aria-label="Realized period"
            onChange={(value) => { setPeriod(value); writePeriod(value) }} />
        </Group>
        <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="md">
          {(cards.data?.rows ?? []).map((row) => <RealizedProfitCard key={row.tool} row={row} />)}
          {cards.data?.total != null && (
            <Stat label="Realized profit total" value={isk(cards.data.total)}
              hint="Tools this session holds" />
          )}
        </SimpleGrid>

        <Group justify="space-between" align="center">
          <Title order={4}>History</Title>
          <SegmentedControl size="xs" value={range} onChange={setRange} data={RANGE_OPTIONS} />
        </Group>

        {showTrading && (
          <HistoryChart title="Trading Realized Profit vs. Production Stock Value" rows={history ?? []}
            lines={[
              { dataKey: 'trading_realized_profit', name: 'Trading Realized Profit', color: COLORS.accent, formatter: isk },
              { dataKey: 'production_stock_value', name: 'Production Stock Value', color: COLORS.info, formatter: isk },
            ]} />
        )}

        {showTrading && (
          <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="md">
            <HistoryChart title="Trading Average Margin" rows={history ?? []}
              lines={[{ dataKey: 'trading_average_margin', name: 'Average Margin', color: COLORS.info, formatter: pct }]} />
            <HistoryChart title="Trading Daily Profit Volatility" rows={history ?? []}
              lines={[{ dataKey: 'trading_daily_profit_volatility', name: 'Daily Profit Volatility', color: COLORS.warn, formatter: isk }]} />
          </SimpleGrid>
        )}

        <HistoryChart title="Total Wealth" rows={history ?? []}
          hint="Assets, blueprints and wallet balances shared with Portfolio"
          lines={[{ dataKey: 'total_wealth', name: 'Total Wealth', color: COLORS.accent, formatter: isk }]} />

        <Divider />
        <ManualPricesSection />
      </Stack>
    </Container>
  )
}
