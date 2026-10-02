import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { Button, Checkbox, Group, MultiSelect, Select, TextInput, NumberInput, Badge, Text, Stack, Title, Paper, Tooltip, UnstyledButton } from '@mantine/core'
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip as ChartTooltip } from 'recharts'
import { IconMinus, IconTrendingDown, IconTrendingUp } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { ShortlistRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { DetailRow, RowDetailDrawer } from '../../components/RowDetailDrawer'
import { useAction } from '../../hooks/useAction'
import { isk, pct, qty } from '../../format'
import { COLORS } from '../../theme'
import { hubLabel, hubSelectData } from '../../tradingHubs'

const ALL_DECISIONS = ['Inactive', 'Missing ID', 'No market data', 'Skip', 'Already ordered', 'Import']
const DECISION_COLOR: Record<string, string> = {
  Import: 'accent',
  'Already ordered': 'info',
  'No market data': 'warn',
  Skip: 'warn',
  Inactive: 'danger',
  'Missing ID': 'danger',
}
const META_UNKNOWN = 'unknown'

export default function Shortlist() {
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['trading', 'shortlist', 'snapshot'], queryFn: tradingApi.shortlistSnapshot })
  const { data: settings } = useQuery({ queryKey: ['trading', 'settings'], queryFn: tradingApi.settings })
  // Zero-network-cost signal (pure local computation over already-persisted
  // Goonmetrics history, see history_backtest.compute_margin_trends) - safe
  // to fetch unconditionally alongside the snapshot, no login/ESI needed.
  const { data: trends } = useQuery({ queryKey: ['trading', 'shortlist', 'trends'], queryFn: tradingApi.shortlistTrends })
  const toggleCap = useAction('Shortlist Cap', tradingApi.updateSettings, [['trading', 'settings']],
    { tier: 'local' })
  const setHub = useAction('Buy Hub', tradingApi.updateSettings, [['trading', 'settings'], ['trading', 'history']],
    { tier: 'local', effect: 'Saves the buy hub. Prices follow on the next Refresh Shortlist.' })
  const recategorize = useAction('Recategorize', tradingApi.recategorizeShortlist, [['trading', 'shortlist', 'snapshot']],
    { tier: 'local', effect: 'Reclassifies Drugs-vs-Implant locally from already-stored category data.' })

  const activeCount = useMemo(() => (data ?? []).filter((r) => r.decision !== 'Inactive').length, [data])

  const categories = useMemo(() => [...new Set((data ?? []).map((r) => r.category))].sort(), [data])
  const metaLevels = useMemo(() => {
    const levels = [...new Set((data ?? []).map((r) => r.meta_level))]
    const nums = levels.filter((l): l is number => l !== null).sort((a, b) => a - b)
    const options = nums.map(String)
    if (levels.includes(null)) options.push(META_UNKNOWN)
    return options
  }, [data])

  const [selCategories, setSelCategories] = useState<string[]>([])
  const [selDecisions, setSelDecisions] = useState<string[]>(ALL_DECISIONS)
  const [selMeta, setSelMeta] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [minMarginPct, setMinMarginPct] = useState<number | ''>(0)
  // Local draft, committed on blur instead of firing a full-settings POST (+
  // toast) on every keystroke/spinner click - confirmed real UX bug: typing
  // "500" over the old value fired 3 separate saves. Synced from `settings`
  // (not per-row state, so - unlike Logistics.tsx's category drafts - there's
  // no "an unrelated edit elsewhere wipes this" risk to worry about here).
  const [capDraft, setCapDraft] = useState<number | ''>(1)
  useEffect(() => {
    if (settings) setCapDraft(settings.max_active_shortlist_items)
  }, [settings?.max_active_shortlist_items])

  // Detail drawer: the open item lives in the URL (?item=<item_id>) so
  // back/forward and shared links reopen it.
  const [searchParams, setSearchParams] = useSearchParams()
  const openItemId = searchParams.get('item')
  const openRow = useMemo(
    () => (openItemId ? (data ?? []).find((r) => String(r.item_id) === openItemId) : undefined),
    [data, openItemId],
  )
  const hub = hubLabel(settings?.jita_region_id)
  const openItem = (row: ShortlistRow) => setSearchParams((p) => { p.set('item', String(row.item_id)); return p })
  const closeItem = () => setSearchParams((p) => { p.delete('item'); return p })

  const effectiveCategories = selCategories.length ? selCategories : categories
  const effectiveMeta = selMeta.length ? selMeta : metaLevels

  const filtered = useMemo(() => {
    return (data ?? []).filter((r) => {
      if (!effectiveCategories.includes(r.category)) return false
      if (!selDecisions.includes(r.decision)) return false
      const metaKey = r.meta_level === null ? META_UNKNOWN : String(r.meta_level)
      if (!effectiveMeta.includes(metaKey)) return false
      if (search && !r.item.toLowerCase().includes(search.toLowerCase())) return false
      if (minMarginPct && (r.margin ?? -Infinity) < Number(minMarginPct) / 100) return false
      return true
    })
  }, [data, effectiveCategories, selDecisions, effectiveMeta, search, minMarginPct])

  // GitHub issue #100: computed from avg_daily_volume (real market-wide
  // traded quantity, Goonmetrics region history for C-J's own home region)
  // - NOT sell_volume/order-book depth (#51) and NOT this trader's own
  // realized sales (#51's own first fix, which left this empty for every
  // not-yet-sold-by-me candidate).
  const topImports = useMemo(() => {
    return filtered
      .filter((r) => r.profit_per_unit !== null && r.avg_daily_volume !== null)
      .map((r) => ({ item: r.item, maxProfitPerDay: (r.profit_per_unit ?? 0) * (r.avg_daily_volume ?? 0) }))
      .sort((a, b) => b.maxProfitPerDay - a.maxProfitPerDay)
      .slice(0, 15)
  }, [filtered])

  const columns = useMemo<ColumnDef<ShortlistRow, any>[]>(() => [
    // Widened from 220 (user feedback, 2026-10-01): long item names were truncated already at
    // the default width. Pin the column from the Columns menu to keep it visible while scrolling.
    {
      header: 'Item', accessorKey: 'item', size: 280,
      meta: {
        copyable: true,
        hoverCard: (r: ShortlistRow) => (
          <Stack gap={2} miw={200}>
            <Text size="sm" fw={600}>{r.item}</Text>
            <DetailRow label={`Cost (${hubLabel(settings?.jita_region_id)})`} value={isk(r.landed_cost)} />
            <DetailRow label="Sale (Structure)" value={isk(r.net_sell)} />
            <DetailRow label="Profit / unit" value={isk(r.profit_per_unit)} />
            <DetailRow label="Margin" value={pct(r.margin)} />
          </Stack>
        ),
      },
    },
    { header: 'Category', accessorKey: 'category', size: 110, meta: { filterable: true } },
    { header: 'Meta Level', accessorKey: 'meta_level', size: 90, cell: (i) => i.getValue() ?? '–' },
    {
      header: 'Status', accessorKey: 'decision', size: 170, meta: { trackChanges: true },
      // Clicking the badge filters the table to that status; clicking it again
      // (when it is the only one selected) shows every status again.
      cell: (i) => {
        const decision = i.getValue() as string
        return (
          <UnstyledButton
            aria-label={`Filter by status ${decision}`}
            onClick={() => setSelDecisions((prev) => (prev.length === 1 && prev[0] === decision ? ALL_DECISIONS : [decision]))}
          >
            <Badge color={DECISION_COLOR[decision] ?? 'gray'} variant="light" style={{ cursor: 'pointer' }}>{decision}</Badge>
          </UnstyledButton>
        )
      },
    },
    {
      header: 'Days Until Auto-Deactivation', accessorKey: 'days_until_deactivation', size: 150,
      cell: (i) => {
        const days = i.getValue() as number | null
        if (days === null) return '–'
        return <Text size="sm" c={days <= 5 ? 'danger' : undefined}>{days}</Text>
      },
    },
    { header: 'Margin', accessorKey: 'margin', size: 90, meta: { trackChanges: true }, cell: (i) => pct(i.getValue()) },
    {
      header: 'Trend (3d vs 30d)', id: 'trend', size: 150,
      accessorFn: (r) => trends?.[r.item_id]?.trend_pct ?? null,
      cell: (i) => {
        const value = i.getValue() as number | null
        if (value === null) return <Text size="sm" c="dimmed">–</Text>
        if (Math.abs(value) < 0.02) {
          return <Group gap={4} wrap="nowrap"><IconMinus size={14} color="var(--mantine-color-dimmed)" /><Text size="sm" c="dimmed">stable</Text></Group>
        }
        const rising = value > 0
        return (
          <Group gap={4} wrap="nowrap">
            {rising ? <IconTrendingUp size={14} color="var(--mantine-color-accent-5)" /> : <IconTrendingDown size={14} color="var(--mantine-color-danger-5)" />}
            <Text size="sm" c={rising ? 'accent' : 'danger'}>{pct(value)}</Text>
          </Group>
        )
      },
    },
    { header: 'Profit / Unit', accessorKey: 'profit_per_unit', size: 120, meta: { trackChanges: true }, cell: (i) => isk(i.getValue()) },
    {
      // GitHub issue #100: real average daily *market-wide* traded quantity
      // (Goonmetrics region history for C-J's own home region), not
      // sell_volume/order-book depth and not this trader's own sales - "–"
      // means Goonmetrics has no history for this item in that region.
      // Shown as its own column so it's visible independent of Profit/Day,
      // which just multiplies this by Profit/Unit.
      header: 'Market Volume (avg/day)', accessorKey: 'avg_daily_volume', size: 150,
      cell: (i) => qty(i.getValue()),
    },
    {
      // GitHub issue #100: profit_per_unit x avg_daily_volume (real
      // market-wide traded quantity from Goonmetrics region history), not
      // sell_volume/order-book depth (#51) and not this trader's own sales
      // (#51's own first fix) - "–" means Goonmetrics has no history for
      // this item in C-J's home region, not a guess derived from listed
      // quantity or a scope limited to this trader alone.
      header: 'Profit / Day (market)', id: 'maxProfitPerDay', size: 160,
      accessorFn: (r) => (r.profit_per_unit !== null && r.avg_daily_volume !== null) ? r.profit_per_unit * r.avg_daily_volume : null,
      cell: (i) => isk(i.getValue()),
    },
    { header: 'Profit / m³', accessorKey: 'profit_per_m3', size: 110, cell: (i) => qty(i.getValue()) },
    // Label follows the configured buy hub (user feedback, 2026-10-01) - was
    // hardcoded "Cost (Jita)" regardless of jita_region_id's actual value.
    { header: `Cost (${hubLabel(settings?.jita_region_id)})`, accessorKey: 'landed_cost', size: 120, cell: (i) => isk(i.getValue()) },
    {
      // Highest hub buy price that still yields profit_per_unit >= 0 (GitHub
      // issue #221). Computed server-side: (net_sell - import_cost) /
      // (1 + broker fee). Comparable to the hub sell price, not to "Cost".
      header: `Breakeven Buy (${hubLabel(settings?.jita_region_id)})`, id: 'breakevenPrice', size: 150,
      accessorFn: (r) => r.breakeven_buy_price,
      cell: (i) => isk(i.getValue()),
      meta: { cellTitle: () => 'Highest hub buy price that still breaks even after broker fee, freight and structure sale fees.' },
    },
    { header: 'Sale (Structure)', accessorKey: 'net_sell', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Listed Qty (Structure)', accessorKey: 'sell_volume', size: 150, cell: (i) => qty(i.getValue()) },
    { header: 'Own Orders', accessorKey: 'own_orders_remaining', size: 110, cell: (i) => qty(i.getValue()) },
  ], [trends, settings?.jita_region_id])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (isError) return <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />
  if (!data || data.length === 0) {
    return <HintCard>No run yet. Click <b>Refresh Shortlist</b> in the side menu to compute margins and buy recommendations for your shortlist.</HintCard>
  }

  return (
    <Stack>
      {settings && (
        <Paper withBorder p="md" radius="md">
          <Group gap="md" align="flex-end" wrap="wrap">
            <Select
              label="Buy hub"
              data={hubSelectData(settings.jita_region_id)}
              value={String(settings.jita_region_id)}
              onChange={(v) => v && Number(v) !== settings.jita_region_id
                && setHub.mutate({ ...settings, jita_region_id: Number(v) })}
              disabled={setHub.isPending}
              allowDeselect={false}
              w={240}
            />
            <Text size="xs" c="dimmed" maw={520}>
              Where this list buys. Applies to the Trading tool only; other tools have their own hub
              setting. New prices appear after the next <b>Refresh Shortlist</b>. Freight cost per m³
              (Settings) is a single value, so adjust it if the route to the structure changes.
            </Text>
          </Group>
        </Paper>
      )}
      {settings && (
        <Paper withBorder p="md" radius="md">
          <Title order={6} mb={4}>Limit active shortlist size</Title>
          <Text size="sm" c="dimmed" mb="sm">
            Cleanup fetches live market data for every active item, so an uncapped list (especially once
            ships and blueprints are in the candidate universe) gets expensive fast. When this is on,
            Search + Add + Clean Up deactivates the least profitable items beyond the limit — they stay
            visible as Inactive, they are not deleted. Off by default.
          </Text>
          <Group gap="xs" align="center">
            <Checkbox
              label="Enforce active-item cap"
              checked={settings.enforce_shortlist_cap}
              disabled={toggleCap.isPending}
              onChange={(e) => toggleCap.mutate({ ...settings, enforce_shortlist_cap: e.currentTarget.checked })}
            />
            <NumberInput
              value={capDraft}
              onChange={(v) => setCapDraft(v === '' ? '' : Number(v))}
              onBlur={() => capDraft !== '' && toggleCap.mutate({ ...settings, max_active_shortlist_items: Number(capDraft) })}
              min={1} step={10} w={100} size="xs"
              disabled={toggleCap.isPending || !settings.enforce_shortlist_cap}
              aria-label="Maximum active shortlist items"
            />
            <Text size="sm">
              active entries (ranked by profit/day)
            </Text>
          </Group>
          <Text size="xs" c="dimmed" mt={6}>
            Currently active: {activeCount} of {data.length} total (deactivated items stay visible as "Inactive" in
            the list, they don't disappear - deselect them in the status filter below).
          </Text>
        </Paper>
      )}

      <Group grow align="flex-end">
        <MultiSelect label="Category" data={categories} value={selCategories} onChange={setSelCategories} placeholder="All" clearable />
        <MultiSelect label="Status" data={ALL_DECISIONS} value={selDecisions} onChange={setSelDecisions} />
        <MultiSelect label="Meta Level" data={metaLevels} value={selMeta} onChange={setSelMeta} placeholder="All" clearable />
        <TextInput label="Search (item)" value={search} onChange={(e) => setSearch(e.currentTarget.value)} />
        <NumberInput label="Min. margin %" value={minMarginPct} onChange={(v) => setMinMarginPct(v === '' ? '' : Number(v))} min={0} step={1} />
      </Group>

      <Group justify="flex-end">
        <Tooltip label={recategorize.tooltip} disabled={!recategorize.tooltip}>
          <Button size="xs" variant="default" onClick={() => recategorize.mutate()} loading={recategorize.isPending}>
            Fix Categories (Drugs vs. Implant)
          </Button>
        </Tooltip>
      </Group>

      <Text size="sm" c="dimmed">{filtered.length} of {data.length} items</Text>

      {filtered.length === 0 ? (
        <HintCard>No items match the current filters.</HintCard>
      ) : (
        <DataTable
          data={filtered} columns={columns} maxHeight={560} dataUpdatedAt={dataUpdatedAt}
          tableId="trading-shortlist" exportFilename="trading-shortlist"
          getRowId={(r) => String(r.item_id)}
          rowDetail={false} onRowClick={openItem} activeRowId={openItemId ?? undefined}
          extraViewState={{
            value: { selCategories, selDecisions, selMeta, search, minMarginPct },
            apply: (v) => {
              const f = v as Partial<{ selCategories: string[]; selDecisions: string[]; selMeta: string[]; search: string; minMarginPct: number | '' }>
              setSelCategories(f.selCategories ?? [])
              setSelDecisions(f.selDecisions ?? ALL_DECISIONS)
              setSelMeta(f.selMeta ?? [])
              setSearch(f.search ?? '')
              setMinMarginPct(f.minMarginPct ?? 0)
            },
          }}
        />
      )}

      <RowDetailDrawer opened={!!openRow} onClose={closeItem} title={openRow?.item ?? ''}>
        {openRow && (
          <>
            <Group gap="xs">
              <Badge color={DECISION_COLOR[openRow.decision] ?? 'gray'} variant="light">{openRow.decision}</Badge>
              <Badge color="gray" variant="outline">{openRow.category}</Badge>
              {!openRow.active && <Badge color="danger" variant="outline">inactive</Badge>}
            </Group>
            <Stack gap={6}>
              <Title order={6} c="dimmed" tt="uppercase">Pricing</Title>
              <DetailRow label={`${hub} sell`} value={isk(openRow.jita_sell)} />
              <DetailRow label="Import cost" value={isk(openRow.import_cost)} />
              <DetailRow label={`Cost (${hub}, landed)`} value={isk(openRow.landed_cost)} />
              <DetailRow label="Sale (Structure, net)" value={isk(openRow.net_sell)} />
              <DetailRow label={`Breakeven buy (${hub})`} value={isk(openRow.breakeven_buy_price)} />
              <DetailRow label="Profit / unit" value={isk(openRow.profit_per_unit)} />
              <DetailRow label="Margin" value={pct(openRow.margin)} />
              <DetailRow label="Profit / m³" value={qty(openRow.profit_per_m3)} />
            </Stack>
            <Stack gap={6}>
              <Title order={6} c="dimmed" tt="uppercase">Market</Title>
              <DetailRow label="Avg. daily volume (market)" value={qty(openRow.avg_daily_volume)} />
              <DetailRow label="Listed qty (Structure)" value={qty(openRow.sell_volume)} />
              <DetailRow label="Own orders" value={qty(openRow.own_orders_remaining)} />
              <DetailRow label="Volume (m³ / unit)" value={qty(openRow.volume_m3)} />
              <DetailRow label="Meta level" value={openRow.meta_level ?? '–'} muted={openRow.meta_level === null} />
              <DetailRow
                label="Days until auto-deactivation"
                value={openRow.days_until_deactivation ?? '–'} muted={openRow.days_until_deactivation === null}
              />
            </Stack>
            <Button component={Link} to={`/trading/history?item=${openRow.item_id}`} variant="default" size="xs">
              Open in Price History
            </Button>
          </>
        )}
      </RowDetailDrawer>

      {topImports.length > 0 && (
        <Stack mt="lg">
          <Title order={6} c="dimmed" tt="uppercase">Top Imports · Max. Profit / Day</Title>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={topImports} layout="vertical" margin={{ left: 120 }}>
              <XAxis type="number" stroke={COLORS.textDim} />
              <YAxis type="category" dataKey="item" width={200} stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
              <ChartTooltip contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }} />
              <Bar dataKey="maxProfitPerDay" fill={COLORS.accent} />
            </BarChart>
          </ResponsiveContainer>
        </Stack>
      )}
    </Stack>
  )
}
