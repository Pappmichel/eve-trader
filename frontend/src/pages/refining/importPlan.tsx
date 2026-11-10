// Import tab shared by Ore & Minerals and Module Reprocessing: the most
// profitable buy list for an ISK budget and/or a haul volume (backend:
// eve_trader/import_planner.py). Each tool's page only picks its API call.
import { useMemo, useState } from 'react'
import {
  Alert, Badge, Button, Card, Group, NumberInput, SimpleGrid, Stack, Text, Title, Tooltip,
} from '@mantine/core'
import { IconAlertTriangle, IconCalculator } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import type { ImportPlan, ImportPlanLine, ImportPlanMineral, ImportPlanRequest } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, iskUnit, pct, qty } from '../../format'

const LIMITED_BY_LABEL: Record<ImportPlan['limited_by'], string> = {
  budget: 'ISK budget',
  volume: 'Volume',
  supply: 'Market supply',
}

function readStored(key: string): number | '' {
  try {
    const raw = window.localStorage.getItem(key)
    const value = raw === null ? NaN : Number(raw)
    return Number.isFinite(value) && value > 0 ? value : ''
  } catch {
    return ''
  }
}

function writeStored(key: string, value: number | '') {
  try {
    if (value === '') window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, String(value))
  } catch {
    // Storage unavailable (private window): the inputs just aren't remembered.
  }
}

interface Props {
  storageKey: string
  showCategory: boolean
  hint: React.ReactNode
  planImport: (req: ImportPlanRequest) => Promise<ImportPlan>
}

export function ImportPlanner({ storageKey, showCategory, hint, planImport }: Props) {
  const [budget, setBudget] = useState<number | ''>(() => readStored(`${storageKey}.budget`))
  const [volumeLimit, setVolumeLimit] = useState<number | ''>(() => readStored(`${storageKey}.volume`))
  const [plan, setPlan] = useState<ImportPlan | null>(null)

  const calculate = useAction('Calculate Import', planImport, [],
    { tier: 'live', effect: 'Prices the active shortlist and reads the hub order books (cached with live fallback).' })

  const setAndStore = (setter: (v: number | '') => void, key: string) => (v: string | number) => {
    const value = v === '' || Number(v) <= 0 ? '' : Number(v)
    setter(value)
    writeStored(`${storageKey}.${key}`, value)
  }

  const lineColumns = useMemo<ColumnDef<ImportPlanLine, any>[]>(() => [
    { header: 'Buy', accessorKey: 'item', size: 240, meta: { copyable: true } },
    ...(showCategory ? [{
      header: 'Type', accessorKey: 'category', size: 115,
      cell: (i: any) => (
        <Badge variant="light" color={i.getValue() === 'module' ? 'accent' : 'gray'}>
          {i.getValue() === 'module' ? 'Module' : 'Ore'}
        </Badge>
      ),
    }] : []),
    { header: 'Hub', accessorKey: 'hub_name', size: 90 },
    { header: 'Units', accessorKey: 'units', size: 110, cell: (i) => qty(i.getValue()) },
    {
      header: 'Bought / Within Cap', accessorKey: 'units_within_cap', size: 210,
      cell: (i) => {
        const r = i.row.original
        return (
          <Tooltip label={`${qty(r.listed_units)} listed in total at the hub`} disabled={r.listed_units === null}>
            <Text size="sm">{qty(r.units)} / {qty(r.units_within_cap)}</Text>
          </Tooltip>
        )
      },
    },
    { header: 'Volume (m3)', accessorKey: 'volume_m3', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'Avg Landed / Unit', accessorKey: 'avg_unit_cost', size: 140, cell: (i) => iskUnit(i.getValue()) },
    { header: 'Max Hub Price', accessorKey: 'max_hub_price', size: 130, cell: (i) => iskUnit(i.getValue()) },
    { header: 'Breakeven Buy', accessorKey: 'breakeven_buy_price', size: 130, cell: (i) => iskUnit(i.getValue()) },
    { header: 'Total Cost', accessorKey: 'cost', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Mineral Value (net)', accessorKey: 'net_value', size: 150, cell: (i) => isk(i.getValue()) },
    { header: 'Profit', accessorKey: 'profit', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Margin', accessorKey: 'margin', size: 90, cell: (i) => pct(i.getValue()) },
    { header: 'Profit / m3', accessorKey: 'profit_per_m3', size: 120, cell: (i) => isk(i.getValue()) },
  ], [showCategory])

  const mineralColumns = useMemo<ColumnDef<ImportPlanMineral, any>[]>(() => [
    { header: 'Mineral', accessorKey: 'name', size: 180 },
    { header: 'Output', accessorKey: 'quantity', size: 140, cell: (i) => qty(i.getValue()) },
    { header: 'C-J Price / Unit', accessorKey: 'unit_value', size: 130, cell: (i) => iskUnit(i.getValue()) },
    { header: 'Value at C-J', accessorKey: 'value', size: 150, cell: (i) => isk(i.getValue()) },
    {
      header: 'Listed at C-J', accessorKey: 'home_listed', size: 170,
      cell: (i) => {
        const r = i.row.original
        if (r.home_listed === null) return <Text size="sm" c="dimmed">unknown</Text>
        return (
          <Group gap={6} wrap="nowrap">
            <Text size="sm">{qty(r.home_listed)}</Text>
            {r.exceeds_listed && (
              <Tooltip label="The output is more than C-J currently lists - selling it all may push the price down.">
                <Badge color="warn" variant="light" leftSection={<IconAlertTriangle size={12} />}>exceeds</Badge>
              </Tooltip>
            )}
          </Group>
        )
      },
    },
  ], [])

  const noLimit = budget === '' && volumeLimit === ''
  const exceeding = plan?.minerals.filter((m) => m.exceeds_listed) ?? []

  return (
    <Stack>
      <HintCard>{hint}</HintCard>

      <Group align="flex-end">
        <NumberInput label="ISK to invest" placeholder="e.g. 5,000,000,000" min={0} thousandSeparator w={240}
          value={budget} onChange={setAndStore(setBudget, 'budget')} />
        <NumberInput label="Volume (m3)" placeholder="e.g. 60,000" min={0} thousandSeparator w={200}
          value={volumeLimit} onChange={setAndStore(setVolumeLimit, 'volume')} />
        <Tooltip label={calculate.tooltip} disabled={!calculate.tooltip} multiline w={280}>
          <Button leftSection={<IconCalculator size={14} />} rightSection={calculate.tierIcon}
            loading={calculate.isPending} disabled={noLimit}
            onClick={() => calculate.mutate(
              { budget_isk: budget === '' ? null : budget, volume_m3: volumeLimit === '' ? null : volumeLimit },
              { onSuccess: (p) => setPlan(p) },
            )}>
            Calculate
          </Button>
        </Tooltip>
        {plan && <Button variant="subtle" onClick={() => setPlan(null)}>Clear Result</Button>}
      </Group>
      <Text size="xs" c="dimmed">Enter a budget, a volume, or both. With both, whichever runs out first stops the list.</Text>

      {plan && (
        <>
          <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }}>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Invested</Text>
              <Title order={4}>{isk(plan.total_cost)}</Title>
              {plan.budget_isk !== null && (
                <Text size="xs" c="dimmed">of {isk(plan.budget_isk)} ({pct(plan.total_cost / plan.budget_isk)})</Text>
              )}
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Volume</Text>
              <Title order={4}>{qty(plan.total_volume_m3)} m3</Title>
              {plan.volume_limit_m3 !== null && (
                <Text size="xs" c="dimmed">
                  of {qty(plan.volume_limit_m3)} m3 ({pct(plan.total_volume_m3 / plan.volume_limit_m3)})
                </Text>
              )}
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Expected Profit</Text>
              <Title order={4}>{isk(plan.total_profit)}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Margin</Text>
              <Title order={4}>{pct(plan.margin)}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Minerals at C-J</Text>
              <Title order={4}>{isk(plan.total_mineral_value)}</Title>
              <Text size="xs" c="dimmed">{isk(plan.total_net_value)} after haircut and refining tax</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Limited By</Text>
              <Title order={4}>{LIMITED_BY_LABEL[plan.limited_by]}</Title>
              <Text size="xs" c="dimmed">
                {plan.profitable_candidates} of {plan.candidates} items have profitable orders
              </Text>
            </Card>
          </SimpleGrid>

          {exceeding.length > 0 && (
            <Alert color="warn" icon={<IconAlertTriangle size={16} />}>
              The output of {exceeding.map((m) => m.name).join(', ')} is more than C-J currently lists. The mineral
              value assumes today's C-J price holds; selling that much may push it down.
            </Alert>
          )}
          {!plan.home_listed_known && (
            <Text size="xs" c="dimmed">
              Mineral prices came from Goonmetrics (no C-J order book access), so the quantity listed at C-J is unknown.
            </Text>
          )}

          <Title order={6} c="dimmed" tt="uppercase" mt="md">Buy List</Title>
          {plan.lines.length === 0 ? (
            <HintCard>Nothing on the active shortlist is profitable at the current hub prices.</HintCard>
          ) : (
            <DataTable data={plan.lines} columns={lineColumns} maxHeight={480}
              getRowId={(r) => String(r.type_id)} exportFilename="import-plan" />
          )}

          {plan.minerals.length > 0 && (
            <>
              <Title order={6} c="dimmed" tt="uppercase" mt="md">Minerals After Reprocessing</Title>
              <DataTable data={plan.minerals} columns={mineralColumns} maxHeight={360}
                getRowId={(r) => String(r.type_id)} exportFilename="import-plan-minerals" />
            </>
          )}
        </>
      )}
    </Stack>
  )
}
