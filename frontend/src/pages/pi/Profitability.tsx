import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Alert, Group, MultiSelect, Select, Stack, Switch, Text } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { piApi } from '../../api/client'
import type { PiProfitRow, PiZone } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { isk, pct, qty } from '../../format'
import { ALL_HUBS } from '../../tradingHubs'
import {
  CC_OPTIONS, QueryError, VerdictBadge, ZONE_OPTIONS, num, reasonLabel, signedPct,
} from './common'

const EMPTY: PiProfitRow[] = []

const columns: ColumnDef<PiProfitRow, any>[] = [
  { header: 'Product', accessorKey: 'product_name', size: 190 },
  { header: 'Tier', accessorFn: (r) => `P${r.tier}`, size: 60 },
  { header: 'Chain', accessorKey: 'chain', size: 90 },
  { header: 'Planet type', accessorFn: (r) => r.planet_type ?? '–', size: 120 },
  {
    header: 'Profit / day', accessorKey: 'profit_per_day', size: 140,
    cell: (i) => isk(i.getValue<number>()),
  },
  {
    header: 'Output / day', accessorKey: 'output_per_day', size: 110,
    cell: (i) => qty(i.getValue<number>()),
  },
  {
    header: 'Verdict', id: 'verdict', size: 230,
    accessorFn: (r) => (r.worth_it ? 'Worth it' : `Not worth it${r.reason ? `: ${reasonLabel(r.reason)}` : ''}`),
    cell: (i) => <VerdictBadge worthIt={i.row.original.worth_it} reason={i.row.original.reason} />,
  },
  {
    header: 'Interactions / week', accessorKey: 'interactions_per_week', size: 130,
    cell: (i) => num(i.getValue<number>(), 1),
  },
  {
    header: 'ISK / interaction', accessorKey: 'isk_per_interaction', size: 130,
    cell: (i) => isk(i.getValue<number | null>()),
  },
  {
    header: 'Price change', id: 'trend_change', size: 110,
    accessorFn: (r) => r.trend?.change ?? null,
    cell: (i) => signedPct(i.getValue<number | null>()),
    meta: { headerHint: 'Change of the average price over the trend window (first vs last week)' },
  },
  {
    header: 'Volatility', id: 'trend_vol', size: 90,
    accessorFn: (r) => r.trend?.volatility ?? null,
    cell: (i) => pct(i.getValue<number | null>()),
  },
  {
    header: 'Trend days', id: 'trend_days', size: 90,
    accessorFn: (r) => r.trend?.days ?? null,
    cell: (i) => i.getValue<number | null>() ?? '–',
  },
  {
    header: 'Market share', accessorKey: 'market_share', size: 100,
    cell: (i) => pct(i.getValue<number | null>()),
    meta: { headerHint: 'Your output as a share of the average daily market volume' },
  },
]

export default function Profitability() {
  const navigate = useNavigate()
  const [zone, setZone] = useState<PiZone | null>(null)
  const [cc, setCc] = useState<string | null>(null)
  const [tiers, setTiers] = useState<string[]>([])
  const [chain, setChain] = useState<string | null>(null)
  const [worthOnly, setWorthOnly] = useState(false)

  const { data, isLoading, isError, error, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['pi', 'profitability', zone, cc],
    queryFn: () => piApi.profitability(zone ?? undefined, cc === null ? undefined : Number(cc)),
  })

  const rows = data?.rows ?? EMPTY
  const chains = useMemo(() => [...new Set(rows.map((r) => r.chain))].sort(), [rows])
  const filtered = useMemo(() => rows.filter((r) =>
    (tiers.length === 0 || tiers.includes(String(r.tier)))
    && (!chain || r.chain === chain)
    && (!worthOnly || r.worth_it)), [rows, tiers, chain, worthOnly])

  const a = data?.assumptions
  return (
    <Stack>
      <Group align="flex-end">
        <Select label="Security zone" w={160} allowDeselect={false} data={ZONE_OPTIONS}
          value={zone ?? data?.zone ?? 'highsec'} onChange={(v) => setZone(v as PiZone)} />
        <Select label="Command Center level" w={180} allowDeselect={false} data={CC_OPTIONS}
          value={cc ?? String(data?.cc_level ?? 5)} onChange={setCc} />
        <MultiSelect label="Tier" w={180} placeholder="All tiers" data={[1, 2, 3, 4].map((t) => ({ value: String(t), label: `P${t}` }))}
          value={tiers} onChange={setTiers} />
        <Select label="Chain" w={150} clearable placeholder="All chains" data={chains} value={chain} onChange={setChain} />
        <Switch label="Worth it only" checked={worthOnly} onChange={(e) => setWorthOnly(e.currentTarget.checked)} mb={6} />
      </Group>

      {a && (
        <Text size="xs" c="dimmed">
          Assumptions: yield {num(a.yield_per_head, 0)}/head/h (effective {num(a.effective_yield_per_head, 0)}),
          program {num(a.program_hours, 0)} h, collected every {num(a.interval_hours, 0)} h,
          customs tax {pct(a.tax_rate)}{a.npc_tax_rate !== undefined && <> (NPC {pct(a.npc_tax_rate)} {a.zone === 'highsec' ? 'in high-sec' : '- none outside high-sec'} + owner {pct(a.owner_tax_rate ?? 0)})</>}, freight {isk(a.freight_per_m3)}/m³,
          prices at {a.market_label ?? (a.hub_region_id === ALL_HUBS ? 'the best hub per item' : `region ${a.hub_region_id}`)}
          {' '}({a.valuation === 'buy_orders' ? 'sold into buy orders' : 'listed as sell orders'}).
          Change them under Settings.
        </Text>
      )}

      {a?.price_note && <Alert color="yellow">Prices: {a.price_note}. Tick "Structure market book" for a character with docking access on the Characters page for live order-book prices.</Alert>}
      {isError && <QueryError error={error} />}
      {!isError && (
        <>
          <HintCard>
            One row per product and (for extraction chains) planet type, using the best design that fits.
            Click a row to open it in the Planner.
          </HintCard>
          <DataTable
            data={filtered}
            columns={columns}
            isLoading={isLoading}
            isError={isError}
            onRetry={() => refetch()}
            dataUpdatedAt={dataUpdatedAt}
            maxHeight={640}
            tableId="pi-profitability"
            exportFilename="pi-profitability"
            emptyLabel="No products match the filters"
            getRowId={(r) => `${r.chain}:${r.product_type_id}:${r.planet_type_id}`}
            onRowClick={(r) => navigate('/pi/planner', {
              state: { prefill: { chain: r.chain, product_type_id: r.product_type_id, planet_type_id: r.planet_type_id } },
            })}
          />
        </>
      )}
    </Stack>
  )
}
