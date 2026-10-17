import type { ReactNode } from 'react'
import { Badge, Card, Group, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import type { RealizedSummary } from '../../../api/types'
import { HintCard } from '../../../components/HintCard'
import { isk, pct, qty } from '../../../format'
import { COLORS } from '../../../theme'
import { compactIsk, profitSeries } from './chart'
import { coverageColor } from './coverage'
import { CoverageBadge } from './flags'
import type { RealizedPeriod } from './period'
import { periodLongerThan90Days } from './period'

const SEGMENT_COLORS = [COLORS.accent, COLORS.info, COLORS.warn, COLORS.danger, COLORS.text, COLORS.textDim, COLORS.border]

function Kpi({ label, value, hint, extra }: { label: string; value: string; hint?: string | null; extra?: ReactNode }) {
  return (
    <Card withBorder padding="sm">
      <Text size="xs" c="dimmed" tt="uppercase">{label}</Text>
      <Title order={3}>{value}</Title>
      {hint && <Text size="xs" c="dimmed">{hint}</Text>}
      {extra}
    </Card>
  )
}

export function OverviewTab({ summary, period }: { summary: RealizedSummary; period: RealizedPeriod }) {
  if (summary.status.empty || summary.status.needs_rebuild) {
    return (
      <HintCard>
        {summary.status.hint || 'Recompute to build realized profit from the archive.'}
      </HintCard>
    )
  }

  const weekly = periodLongerThan90Days(period, summary.status.cutover_t0)
  const points = profitSeries(summary.by_day, weekly)
  const structure = summary.cost_structure
  const spending = summary.spending
  const structureTotal = structure.reduce((sum, line) => sum + (line.amount > 0 ? line.amount : 0), 0)
  const coverage = coverageColor(summary.coverage)

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, xs: 2, md: 3 }}>
        <Kpi label="Revenue" value={isk(summary.revenue)} />
        <Kpi label="Cost of goods sold" value={isk(summary.cost_of_goods_sold)} />
        <Kpi
          label="Realized profit"
          value={isk(summary.realized_profit)}
          extra={(
            <Stack gap={4} mt={4}>
              {summary.estimated && <Badge color="warn" variant="light" w="fit-content">estimated</Badge>}
              {summary.flags.price_missing && <Badge color="danger" variant="light" w="fit-content">price missing</Badge>}
              {summary.tax_missing_sales > 0 && (
                <Badge color="warn" variant="light" w="fit-content">tax missing ({summary.tax_missing_sales} sales)</Badge>
              )}
              <Text size="xs" c="dimmed">
                Manual costs {isk(summary.manual_costs)} · Manual revenues {isk(summary.manual_revenues)}
              </Text>
              <Text size="xs">Profit including manual entries {isk(summary.profit_including_manual)}</Text>
            </Stack>
          )}
        />
        <Kpi label="Margin" value={summary.margin == null ? '–' : pct(summary.margin)} />
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed" tt="uppercase">Coverage</Text>
          <Title order={3} c={coverage ?? undefined}>{summary.coverage == null ? '–' : pct(summary.coverage)}</Title>
          <CoverageBadge coverage={summary.coverage} />
        </Card>
        <Kpi label="Units sold" value={qty(summary.units_sold)} />
      </SimpleGrid>

      <div>
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">Not part of realized profit</Title>
        <SimpleGrid cols={{ base: 1, xs: 2, md: 3 }}>
          <Kpi label="Fees without sale" value={isk(summary.fees_without_sale)} />
          <Kpi label="Fees on other trading" value={isk(summary.fees_on_other_trading)} />
          <Kpi
            label="Capital in BPOs"
            value={isk(summary.capital_in_bpos)}
            hint={summary.balances_as_of_rebuild.includes('capital_in_bpos') ? 'Balance as of the last recompute' : null}
          />
          <Kpi
            label="Inventory at cost"
            value={isk(summary.inventory_at_cost)}
            hint={summary.balances_as_of_rebuild.includes('inventory_at_cost') ? 'Balance as of the last recompute' : null}
          />
          <Kpi label="Cancelled-job losses" value={isk(summary.cancelled_job_losses)} />
          <Kpi label="Failed-invention losses" value={isk(summary.failed_invention_losses)} />
        </SimpleGrid>
      </div>

      {spending && (
        <div>
          <Title order={6} c="dimmed" tt="uppercase" mb={4}>Spent on jobs in this period</Title>
          <Text size="xs" c="dimmed" mb="xs">
            Jobs started in the period. Most of this is still in stock or in running jobs, so it is not in the cost of goods sold yet.
          </Text>
          <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }}>
            <Kpi
              label="Job fees paid"
              value={isk(spending.job_fees_total)}
              extra={(
                <Stack gap={0} mt={4}>
                  {spending.job_fees.map((line) => (
                    <Text key={line.activity_id} size="xs" c="dimmed">
                      {line.label ?? `Activity ${line.activity_id}`}: {isk(line.amount)} ({qty(line.jobs)} jobs)
                    </Text>
                  ))}
                </Stack>
              )}
            />
            <Kpi label="Invention (inputs and fees)" value={isk(spending.invention_total)} />
            <Kpi
              label="Produced, still in stock"
              value={isk(spending.produced_in_stock)}
              hint="At cost · balance as of the last recompute"
            />
            <Kpi
              label="In running jobs"
              value={isk(spending.in_running_jobs)}
              hint={`${qty(spending.running_jobs)} jobs · balance as of the last recompute`}
            />
          </SimpleGrid>
        </div>
      )}

      <div>
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">
          {weekly ? 'Realized profit per week' : 'Realized profit per day'}
        </Title>
        {points.length === 0 ? (
          <HintCard>No sales in this period.</HintCard>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={points}>
              <CartesianGrid stroke={COLORS.border} />
              <XAxis dataKey="date" stroke={COLORS.textDim} tick={{ fontSize: 11 }} />
              <YAxis stroke={COLORS.textDim} tick={{ fontSize: 11 }} tickFormatter={(value) => compactIsk(Number(value))} width={64} />
              <Tooltip
                contentStyle={{ background: COLORS.surface2, border: `1px solid ${COLORS.border}` }}
                formatter={(value, name) => [isk(Number(value)), name === 'cumulative' ? 'Cumulative' : 'Profit']}
              />
              <Bar dataKey="profit" fill={COLORS.accent} name="Profit" />
              <Line type="monotone" dataKey="cumulative" stroke={COLORS.info} dot={false} name="Cumulative" />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      <div>
        <Title order={6} c="dimmed" tt="uppercase" mb={4}>Cost of goods sold breakdown</Title>
        <Text size="xs" c="dimmed" mb="xs">
          What the units sold in this period cost. Opening and unknown stock is one planner value per unit, so build costs inside it are not split into job costs or invention.
        </Text>
        <div
          aria-label="Cost structure"
          style={{ display: 'flex', height: 18, width: '100%', background: COLORS.surface2, borderRadius: 4, overflow: 'hidden' }}
        >
          {structure.map((line, index) => (
            line.amount > 0 && structureTotal > 0 ? (
              <div
                key={line.key}
                title={`${line.label}: ${isk(line.amount)}`}
                style={{ width: `${(line.amount / structureTotal) * 100}%`, background: SEGMENT_COLORS[index % SEGMENT_COLORS.length] }}
              />
            ) : null
          ))}
        </div>
        <Group gap="md" mt="xs">
          {structure.map((line, index) => (
            <Group key={line.key} gap={6} wrap="nowrap">
              <span style={{ width: 10, height: 10, background: SEGMENT_COLORS[index % SEGMENT_COLORS.length], display: 'inline-block' }} />
              <Text size="xs">{line.label}</Text>
            </Group>
          ))}
        </Group>
        <Table mt="sm" withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Component</Table.Th>
              <Table.Th>Amount</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {structure.map((line) => (
              <Table.Tr key={line.key}>
                <Table.Td>{line.label}</Table.Td>
                <Table.Td>{isk(line.amount)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </div>
    </Stack>
  )
}
