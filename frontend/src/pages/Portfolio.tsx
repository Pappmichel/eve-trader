import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Container, Title, Text, SimpleGrid, Card, Group, Stack, Button, Loader, Center, SegmentedControl,
  NumberInput, ActionIcon, Divider, Badge, Anchor, Tabs, Progress, ScrollArea,
} from '@mantine/core'
import { IconArrowLeft, IconTrash, IconCheck } from '@tabler/icons-react'
import { modals } from '@mantine/modals'
import { Link } from 'react-router-dom'
import { Bar, ComposedChart, LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid, Legend } from 'recharts'
import type { ColumnDef } from '@tanstack/react-table'

import { portfolioApi } from '../api/client'
import { DataTable } from '../components/DataTable'
import { HintCard } from '../components/HintCard'
import { SearchableSelect } from '../components/SearchableSelect'
import { useAction } from '../hooks/useAction'
import { useItemNameOptions } from '../hooks/useStaticOptions'
import { isk, qty, pct, dateTime } from '../format'
import { CHANNEL_COLORS, COLORS } from '../theme'
import { toolLabel } from '../esiRegistry'
import type { AttentionItem, ManualItemPriceRow, PortfolioCapital, PortfolioSnapshotRow, RealizedHistory, RealizedToolCard, WealthBreakdown, WealthChange, WealthShareRow } from '../api/types'
import { CoverageBadge } from './production/realized/flags'

const PERIOD_KEY = 'portfolio-realized-period'
const TAB_KEY = 'portfolio-wealth-tab'
const WEALTH_TABS = ['owner', 'location', 'category', 'top'] as const
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

function readTab(): string {
  try {
    const value = localStorage.getItem(TAB_KEY)
    if (value === 'owner' || value === 'location' || value === 'category' || value === 'top') return value
  } catch {
    // Private mode can throw. The owner tab is the default.
  }
  return 'owner'
}

function writeTab(value: string | null) {
  if (!value || !WEALTH_TABS.includes(value as typeof WEALTH_TABS[number])) return
  try {
    localStorage.setItem(TAB_KEY, value)
  } catch {
    // Remembering the tab is optional.
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

function AttentionStrip({ items }: { items: AttentionItem[] | undefined }) {
  if (!items || items.length === 0) return null
  return (
    <Stack gap={4}>
      {items.map((item) => (
        item.link.startsWith('#')
          ? <Anchor key={item.key} href={item.link} size="sm">{item.text}</Anchor>
          : <Anchor key={item.key} component={Link} to={item.link} size="sm">{item.text}</Anchor>
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
const WEALTH_KEY = [['portfolio', 'wealth'], ['portfolio', 'breakdown']]

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

function ShareList({ rows }: { rows: WealthShareRow[] }) {
  if (rows.length === 0) return <Text size="sm" c="dimmed">Nothing priced in this view.</Text>
  return (
    <Stack gap="sm">
      {rows.map((row) => (
        <Stack key={row.label} gap={2}>
          <Group justify="space-between">
            <Text size="sm">{row.label}</Text>
            <Text size="sm">{isk(row.value)} · {pct(row.share)}</Text>
          </Group>
          <Progress value={(row.share ?? 0) * 100} size="sm" color="accent" />
        </Stack>
      ))}
    </Stack>
  )
}

function WealthBreakdownSection({ breakdown }: { breakdown: WealthBreakdown | undefined }) {
  const [tab, setTab] = useState(readTab)
  const setPrice = useAction('Set manual price', (args: { itemName: string; price: number }) =>
    portfolioApi.setManualPrice(args.itemName, args.price), [['portfolio', 'wealth'], ['portfolio', 'breakdown']])
  if (!breakdown) return null
  const unpriced = breakdown.unpriced ?? []
  return (
    <Stack gap="md">
      <Tabs value={tab} onChange={(value) => { setTab(value ?? 'owner'); writeTab(value) }}>
        <Tabs.List>
          <Tabs.Tab value="owner">By owner</Tabs.Tab>
          <Tabs.Tab value="location">By location</Tabs.Tab>
          <Tabs.Tab value="category">By category</Tabs.Tab>
          <Tabs.Tab value="top">Top 20</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="owner" pt="sm"><ShareList rows={breakdown.by_owner} /></Tabs.Panel>
        <Tabs.Panel value="location" pt="sm"><ShareList rows={breakdown.by_location} /></Tabs.Panel>
        <Tabs.Panel value="category" pt="sm"><ShareList rows={breakdown.by_category} /></Tabs.Panel>
        <Tabs.Panel value="top" pt="sm">
          {breakdown.top.length === 0 ? <Text size="sm" c="dimmed">Nothing priced in this view.</Text> : (
            <Stack gap="xs">
              {breakdown.top.map((row) => (
                <Group key={`${row.component}-${row.type_id}-${row.owner_name}-${row.location_name}`} justify="space-between">
                  <Text size="sm">{row.type_name} · {row.owner_name} · {row.location_name}</Text>
                  <Text size="sm">{qty(row.quantity)} · {isk(row.value)} · {pct(row.share)}</Text>
                </Group>
              ))}
            </Stack>
          )}
        </Tabs.Panel>
      </Tabs>

      {unpriced.length > 0 && (
        <Stack id="unpriced" gap="xs">
          <Title order={6}>Unpriced</Title>
          <Text size="xs" c="dimmed">
            Excluded from the total until a manual price is set. One row per type
            {breakdown.unpriced_types > unpriced.length
              ? `, largest quantity first: ${unpriced.length} of ${breakdown.unpriced_types} types shown.`
              : '.'}
          </Text>
          {/* Fixed height: up to 100 rows would otherwise push everything below off the page. */}
          <ScrollArea.Autosize mah={320} type="auto" offsetScrollbars>
            <Stack gap="xs">
              {unpriced.map((row) => (
                <Group key={`${row.component}-${row.type_id}-${row.owner_name}-${row.location_name}`} justify="space-between">
                  <Text size="sm">{row.type_name ?? `Type ${row.type_id}`} · {row.owner_name} · {row.location_name} · {qty(row.quantity)}</Text>
                  <NumberInput aria-label={`Manual price for ${row.type_name ?? row.type_id}`} min={0} w={160}
                    disabled={!row.type_name || setPrice.isPending}
                    onBlur={(event) => {
                      const price = Number(event.currentTarget.value)
                      if (!row.type_name || event.currentTarget.value === '' || Number.isNaN(price)) return
                      setPrice.mutate({ itemName: row.type_name, price })
                    }} />
                </Group>
              ))}
            </Stack>
          </ScrollArea.Autosize>
        </Stack>
      )}
    </Stack>
  )
}

function TotalWealthSection({ breakdown }: { breakdown: WealthBreakdown | undefined }) {
  const { data } = useQuery({ queryKey: ['portfolio', 'wealth'], queryFn: portfolioApi.wealth })
  if (!data) return null
  const lines: { label: string; value: number | null; hint?: string; synced?: string | null }[] = [
    { label: 'Assets', value: data.wealth_assets_value, synced: data.sync?.assets },
    { label: 'Blueprints', value: data.wealth_blueprints_value, synced: data.sync?.blueprints,
      hint: 'One market quote per BPO, regardless of its own ME/TE. A BPC counts only with a manual price.' },
    { label: 'Listed', value: data.wealth_listed_value, synced: data.sync?.listed,
      hint: 'Open sell orders. ESI leaves these out of the asset list.' },
    { label: 'Contracts', value: data.wealth_contract_value, synced: data.sync?.contracts,
      hint: 'Included items of outstanding contracts this owner issued.' },
    { label: 'Wallet', value: data.wealth_wallet_balance, synced: data.sync?.wallet },
    { label: 'Escrow', value: data.wealth_escrow, synced: data.sync?.escrow,
      hint: 'Buy-order escrow. Shown as – until an order sync stores it.' },
  ]

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }} spacing="md">
        {lines.map((line) => (
          <Stat key={line.label} label={line.label} value={isk(line.value)}
            hint={[line.hint, line.synced ? `Synced ${dateTime(line.synced)}` : undefined].filter(Boolean).join(' ')} />
        ))}
      </SimpleGrid>
      <Text size="xs" c="dimmed">
        Prices: a manual price if set, otherwise the cheaper of the C-J sell price and the Jita sell price
        plus freight ({isk(data.freight_per_m3 ?? null)}/m³, packaged volume) - what it would cost to buy the
        item in again, as the Production buy list does. Listed items use the same price, not their own
        order price, so a markup above Jita plus freight is not counted. Blueprint copies count only with a
        manual price. Capital below shows listed items at their order price.
      </Text>
      <WealthBreakdownSection breakdown={breakdown} />
    </Stack>
  )
}

function CapitalSection({ capital }: { capital: PortfolioCapital | undefined }) {
  if (!capital) return null
  return (
    <Stack gap="sm">
      <Title order={4}>Capital</Title>
      <Text size="xs" c="dimmed">From the {capital.source}. Cost is the oldest layer of that type.</Text>
      {capital.buckets.map((row) => (
        <Card key={row.key} withBorder padding="sm" radius="md" className="et-panel">
          <Group justify="space-between" align="flex-start">
            <Text fw={600}>{row.label}</Text>
            <Group gap="md">
              <Text size="sm">Cost {isk(row.cost_basis)}</Text>
              <Text size="sm">Market {isk(row.market_value)}</Text>
              <Text size="sm">Unrealized {isk(row.unrealized)}</Text>
              <CoverageBadge coverage={row.coverage} />
              {row.estimated && <Badge size="xs" color="warn" variant="light">estimated</Badge>}
            </Group>
          </Group>
          {row.comparison_value != null && (
            <Text size="xs" c="dimmed" mt={4}>Wealth price {isk(row.comparison_value)}</Text>
          )}
        </Card>
      ))}
      <Text size="sm">
        Total cost {isk(capital.total.cost_basis)} · Market {isk(capital.total.market_value)} · Unrealized {isk(capital.total.unrealized)}
      </Text>
      {capital.total.excluded.length > 0 && (
        <Text size="xs" c="dimmed">
          Without {capital.buckets.filter((row) => capital.total.excluded.includes(row.key)).map((row) => row.label).join(', ')}
          {' '}(no market value).
        </Text>
      )}
    </Stack>
  )
}

function RealizedHistoryChart({ history }: { history: RealizedHistory | undefined }) {
  const rows = history?.rows ?? []
  const channels = history?.channels ?? []
  const title = history?.bucket === 'week' ? 'Realized profit per week' : 'Realized profit per day'
  if (rows.length === 0) {
    return (
      <Card withBorder padding="lg" radius="md" className="et-panel">
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">{title}</Title>
        <HintCard>No realized profit in this period.</HintCard>
      </Card>
    )
  }
  return (
    <Card withBorder padding="lg" radius="md" className="et-panel">
      <Title order={6} c="dimmed" tt="uppercase" mb="xs">{title}</Title>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={rows}>
          <CartesianGrid stroke={COLORS.border} />
          <XAxis dataKey="date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
          <YAxis stroke={COLORS.textDim} tick={{ fontSize: 11 }} width={90} tickFormatter={(v: number) => isk(v)} />
          <Tooltip contentStyle={tooltipStyle()} formatter={(v, name) => [typeof v === 'number' ? isk(v) : v, name]} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {channels.map((channel) => (
            <Bar key={channel} dataKey={channel} name={toolLabel(channel)} stackId="profit"
              fill={CHANNEL_COLORS[channel] ?? COLORS.textDim} />
          ))}
          <Line type="monotone" dataKey="cumulative" name="Cumulative" stroke={COLORS.text} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </Card>
  )
}

export default function Portfolio() {
  const attention = useQuery({ queryKey: ['portfolio', 'attention'], queryFn: portfolioApi.attention })
  // Not the overview: that also computes Production's stock value (~10 s) the page no longer shows.
  const change = useQuery({ queryKey: ['portfolio', 'wealth-change'], queryFn: portfolioApi.wealthChange })
  const wealth = useQuery({ queryKey: ['portfolio', 'wealth'], queryFn: portfolioApi.wealth })
  const breakdown = useQuery({ queryKey: ['portfolio', 'breakdown'], queryFn: portfolioApi.wealthBreakdown })
  const capital = useQuery({ queryKey: ['portfolio', 'capital'], queryFn: portfolioApi.capital })
  const [period, setPeriod] = useState(readPeriod)
  const cards = useQuery({
    queryKey: ['portfolio', 'realized-cards', period],
    queryFn: () => portfolioApi.realizedCards(period),
    retry: false,
  })
  const realized = useQuery({
    queryKey: ['portfolio', 'realized-history', period],
    queryFn: () => portfolioApi.realizedHistory(period),
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
        <AttentionStrip items={attention.data?.items} />
        {wealth.isLoading && <Center h={80}><Loader color="accent" /></Center>}
        {wealth.data && (
          <Card withBorder padding="lg" radius="md" className="et-panel">
            <Title order={6} c="dimmed" tt="uppercase" mb="xs">Total Wealth</Title>
            <Text size="xl" fw={700}>{isk(wealth.data.total_wealth)}</Text>
            <WealthChanges change={change.data} />
          </Card>
        )}
        <TotalWealthSection breakdown={breakdown.data} />

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
        {cards.data?.freight && (
          <Text size="sm">
            Freight from couriers {isk(cards.data.freight.courier)} · configured rate {isk(cards.data.freight.configured)}
          </Text>
        )}

        <RealizedHistoryChart history={realized.data} />
        <CapitalSection capital={capital.data} />

        <Group justify="space-between" align="center">
          <Title order={4}>Total Wealth history</Title>
          <SegmentedControl size="xs" value={range} onChange={setRange} data={RANGE_OPTIONS} aria-label="Wealth history range" />
        </Group>

        <HistoryChart title="Total Wealth" rows={history ?? []}
          hint="Assets, blueprints, listed orders, contracts, wallet and escrow shared with Portfolio. Older rows leave the new lines blank."
          lines={[
            { dataKey: 'total_wealth', name: 'Total Wealth', color: COLORS.accent, formatter: isk },
            { dataKey: 'wealth_listed_value', name: 'Listed', color: COLORS.info, formatter: isk },
            { dataKey: 'wealth_contract_value', name: 'Contracts', color: COLORS.warn, formatter: isk },
            { dataKey: 'wealth_escrow', name: 'Escrow', color: COLORS.danger, formatter: isk },
          ]} />

        <Divider />
        <ManualPricesSection />
      </Stack>
    </Container>
  )
}
