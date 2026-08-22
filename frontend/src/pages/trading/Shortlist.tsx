import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Checkbox, Group, MultiSelect, TextInput, NumberInput, Badge, Text, Stack, Title } from '@mantine/core'
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts'
import { IconMinus, IconTrendingDown, IconTrendingUp } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { tradingApi } from '../../api/client'
import type { ShortlistRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, pct, qty } from '../../format'
import { COLORS } from '../../theme'

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
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['trading', 'shortlist', 'snapshot'], queryFn: tradingApi.shortlistSnapshot })
  const { data: settings } = useQuery({ queryKey: ['trading', 'settings'], queryFn: tradingApi.settings })
  // Zero-network-cost signal (pure local computation over already-persisted
  // Goonmetrics history, see history_backtest.compute_margin_trends) - safe
  // to fetch unconditionally alongside the snapshot, no login/ESI needed.
  const { data: trends } = useQuery({ queryKey: ['trading', 'shortlist', 'trends'], queryFn: tradingApi.shortlistTrends })
  const toggleCap = useAction('Shortlist Cap', tradingApi.updateSettings, [['trading', 'settings']])
  const recategorize = useAction('Recategorize', tradingApi.recategorizeShortlist, [['trading', 'shortlist', 'snapshot']])

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

  // GitHub issue #51: computed from avg_daily_sold (real observed sales,
  // from the last Reconcile Trades run) - NOT sell_volume/order-book depth,
  // which used to make a never-actually-sold item with a big listed
  // quantity show a wildly inflated "Profit / Day".
  const topImports = useMemo(() => {
    return filtered
      .filter((r) => r.profit_per_unit !== null && r.avg_daily_sold !== null)
      .map((r) => ({ item: r.item, maxProfitPerDay: (r.profit_per_unit ?? 0) * (r.avg_daily_sold ?? 0) }))
      .sort((a, b) => b.maxProfitPerDay - a.maxProfitPerDay)
      .slice(0, 15)
  }, [filtered])

  // GitHub issue #52: meta.mobileHide marks columns DataTable force-hides
  // below the `sm` breakpoint - keeps Item/Status/Margin/Profit-per-Unit
  // visible (the columns needed to judge a row at a glance on a phone),
  // hides the rest (still reachable via the Columns menu or a wider screen).
  const columns = useMemo<ColumnDef<ShortlistRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'item', size: 220 },
    { header: 'Category', accessorKey: 'category', size: 110, meta: { mobileHide: true } },
    { header: 'Meta Level', accessorKey: 'meta_level', size: 90, cell: (i) => i.getValue() ?? '–', meta: { mobileHide: true } },
    {
      header: 'Status', accessorKey: 'decision', size: 170,
      cell: (i) => <Badge color={DECISION_COLOR[i.getValue() as string] ?? 'gray'} variant="light">{i.getValue()}</Badge>,
    },
    {
      header: 'Days Until Auto-Deactivation', accessorKey: 'days_until_deactivation', size: 150,
      cell: (i) => {
        const days = i.getValue() as number | null
        if (days === null) return '–'
        return <Text size="sm" c={days <= 5 ? 'danger' : undefined}>{days}</Text>
      },
      meta: { mobileHide: true },
    },
    { header: 'Margin', accessorKey: 'margin', size: 90, cell: (i) => pct(i.getValue()) },
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
      meta: { mobileHide: true },
    },
    { header: 'Profit / Unit', accessorKey: 'profit_per_unit', size: 120, cell: (i) => isk(i.getValue()) },
    {
      // GitHub issue #51: profit_per_unit x avg_daily_sold (real observed
      // sales from the last Reconcile Trades run), not sell_volume/
      // order-book depth - "–" means no real sale has been matched for this
      // item yet, not a guess derived from listed quantity.
      header: 'Profit / Day (avg. sold)', id: 'maxProfitPerDay', size: 160,
      accessorFn: (r) => (r.profit_per_unit !== null && r.avg_daily_sold !== null) ? r.profit_per_unit * r.avg_daily_sold : null,
      cell: (i) => isk(i.getValue()),
      meta: { mobileHide: true },
    },
    { header: 'Profit / m³', accessorKey: 'profit_per_m3', size: 110, cell: (i) => qty(i.getValue()), meta: { mobileHide: true } },
    { header: 'Cost (Jita)', accessorKey: 'landed_cost', size: 120, cell: (i) => isk(i.getValue()), meta: { mobileHide: true } },
    { header: 'Sale (Structure)', accessorKey: 'net_sell', size: 140, cell: (i) => isk(i.getValue()), meta: { mobileHide: true } },
    { header: 'Listed Qty (Structure)', accessorKey: 'sell_volume', size: 150, cell: (i) => qty(i.getValue()), meta: { mobileHide: true } },
    { header: 'Own Orders', accessorKey: 'own_orders_remaining', size: 110, cell: (i) => qty(i.getValue()), meta: { mobileHide: true } },
  ], [trends])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={560} />
  if (isError) return <DataTable data={[]} columns={columns} isError onRetry={() => refetch()} maxHeight={560} />
  if (!data || data.length === 0) {
    return <HintCard>No run yet. Click <b>Refresh Shortlist</b> on the left to compute margins and buy recommendations for your shortlist.</HintCard>
  }

  return (
    <Stack>
      {settings && (
        <Stack gap={4}>
          <Group gap="xs" align="center">
            <Checkbox
              label="Cap shortlist at max."
              checked={settings.enforce_shortlist_cap}
              disabled={toggleCap.isPending}
              onChange={(e) => toggleCap.mutate({ ...settings, enforce_shortlist_cap: e.currentTarget.checked })}
            />
            <NumberInput
              value={capDraft}
              onChange={(v) => setCapDraft(v === '' ? '' : Number(v))}
              onBlur={() => capDraft !== '' && toggleCap.mutate({ ...settings, max_active_shortlist_items: Number(capDraft) })}
              min={1} step={10} w={100} size="xs"
              disabled={toggleCap.isPending}
            />
            <Text size="sm">
              active entries (by profit/day, excess items are deactivated by "Search + Add + Clean Up")
            </Text>
          </Group>
          <Text size="xs" c="dimmed">
            Currently active: {activeCount} of {data.length} total (deactivated items stay visible as "Inactive" in
            the list, they don't disappear - deselect them in the status filter below).
          </Text>
        </Stack>
      )}

      <Group grow align="flex-end">
        <MultiSelect label="Category" data={categories} value={selCategories} onChange={setSelCategories} placeholder="All" clearable />
        <MultiSelect label="Status" data={ALL_DECISIONS} value={selDecisions} onChange={setSelDecisions} />
        <MultiSelect label="Meta Level" data={metaLevels} value={selMeta} onChange={setSelMeta} placeholder="All" clearable />
        <TextInput label="Search (item)" value={search} onChange={(e) => setSearch(e.currentTarget.value)} />
        <NumberInput label="Min. margin %" value={minMarginPct} onChange={(v) => setMinMarginPct(v === '' ? '' : Number(v))} min={0} step={1} />
      </Group>

      <Group justify="flex-end">
        <Button size="xs" variant="default" onClick={() => recategorize.mutate()} loading={recategorize.isPending}>
          Fix Categories (Drugs vs. Implant)
        </Button>
      </Group>

      <Text size="sm" c="dimmed">{filtered.length} of {data.length} items</Text>

      {filtered.length === 0 ? (
        <HintCard>No items match the current filters.</HintCard>
      ) : (
        <DataTable data={filtered} columns={columns} maxHeight={560} />
      )}

      {topImports.length > 0 && (
        <Stack mt="lg">
          <Title order={6} c="dimmed" tt="uppercase">Top Imports · Max. Profit / Day</Title>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={topImports} layout="vertical" margin={{ left: 120 }}>
              <XAxis type="number" stroke={COLORS.textDim} />
              <YAxis type="category" dataKey="item" width={200} stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }} />
              <Bar dataKey="maxProfitPerDay" fill={COLORS.accent} />
            </BarChart>
          </ResponsiveContainer>
        </Stack>
      )}
    </Stack>
  )
}
