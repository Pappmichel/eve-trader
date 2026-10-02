import { useMemo, useState } from 'react'
import { Alert, Badge, Button, Card, Group, SimpleGrid, Stack, Text, Textarea, Title, Tooltip } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { refiningApi } from '../../api/client'
import type { ReprocessingMineralTotal, ReprocessingQuoteResult, ReprocessingQuoteRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { isk, qty } from '../../format'

const DECISION_COLOR: Record<string, string> = {
  Reprocess: 'accent',
  'Sell instead': 'info',
  'Not reprocessable': 'gray',
  'No market data': 'warn',
  'Unknown item': 'danger',
}

export default function ReprocessingQuote() {
  const [paste, setPaste] = useState('')
  const [result, setResult] = useState<ReprocessingQuoteResult | null>(null)
  const quote = useAction('Get Quote', (text: string) => refiningApi.quoteReprocessing(text), [],
    { tier: 'live', effect: 'Prices every pasted item live via ESI (with a Goonmetrics fallback) at the C-J structure.' })

  const columns = useMemo<ColumnDef<ReprocessingQuoteRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'name', size: 220, meta: { copyable: true } },
    { header: 'Qty', accessorKey: 'quantity', size: 90, cell: (i) => qty(i.getValue()) },
    { header: 'Category', accessorKey: 'category', size: 120, meta: { filterable: true } },
    {
      header: 'Recommendation', accessorKey: 'decision', size: 150,
      cell: (i) => <Badge color={DECISION_COLOR[i.getValue() as string] ?? 'gray'} variant="light">{i.getValue()}</Badge>,
    },
    { header: 'Sell As-Is (C-J)', accessorKey: 'sell_as_is_value', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Refined Value (C-J)', accessorKey: 'refined_value', size: 150, cell: (i) => isk(i.getValue()) },
    { header: 'Refining Tax', accessorKey: 'refining_tax', size: 110, cell: (i) => isk(i.getValue()) },
    {
      header: 'Note', accessorKey: 'error', size: 200,
      cell: (i) => (i.getValue() ? <Text size="xs" c="dimmed">{i.getValue() as string}</Text> : null),
    },
  ], [])

  const mineralColumns = useMemo<ColumnDef<ReprocessingMineralTotal, any>[]>(() => [
    { header: 'Mineral', accessorKey: 'name', size: 160 },
    { header: 'Quantity', accessorKey: 'quantity', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'Unit Sell Price (C-J)', accessorKey: 'unit_sell_price', size: 160, cell: (i) => isk(i.getValue()) },
    { header: 'Value (C-J)', accessorKey: 'value', size: 150, cell: (i) => isk(i.getValue()) },
  ], [])

  return (
    <Stack>
      <HintCard>
        Paste from an EVE Inventory window's list view (Ctrl+A, Ctrl+C) - for ratting loot (T1/meta modules,
        ammo, drones). Uses the scrapmetal reprocessing path (fixed 50% + Scrapmetal Processing skill only).
      </HintCard>

      <Textarea
        label="Paste items here" placeholder={'Antimatter Charge S\t1000\tProjectile Ammo\tCharge\t\t\t5.0 m3\t\t'}
        autosize minRows={6} maxRows={16} value={paste} onChange={(e) => setPaste(e.currentTarget.value)}
        styles={{ input: { fontFamily: 'monospace' } }}
      />

      <Group>
        <Tooltip label={quote.tooltip} disabled={!quote.tooltip} multiline w={280}>
          <Button leftSection={quote.tierIcon} loading={quote.isPending} disabled={!paste.trim()}
            onClick={() => quote.mutate(paste, { onSuccess: (r) => setResult(r) })}>
            Get Quote
          </Button>
        </Tooltip>
        {result && (
          <Button variant="subtle" onClick={() => { setPaste(''); setResult(null) }}>
            Clear
          </Button>
        )}
      </Group>

      {result && (
        <>
          {result.priced_via_fallback && (
            <Alert color="warn" variant="light">
              Structure prices used the Goonmetrics fallback (no seller logged in, or the real order book was
              unavailable) - a less precise community snapshot, not the real order-book percentile.
            </Alert>
          )}
          <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }}>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Marked "Reprocess"</Text>
              <Title order={4}>{result.totals.reprocess_count}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Total Mineral Value</Text>
              <Title order={4}>{isk(result.totals.total_mineral_value)}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed" tt="uppercase">Total Refined Value (after tax)</Text>
              <Title order={4}>{isk(result.totals.total_refined_value)}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Tooltip
                label={'Same "Reprocess"-marked items as the two cards to the left - what you\'d have gotten selling them as-is instead, for a fair comparison against Refined Value.'}
                multiline
                w={260}
              >
                <Text size="xs" c="dimmed" tt="uppercase">Sell As-Is Value (Reprocess Items)</Text>
              </Tooltip>
              <Title order={4}>{isk(result.totals.total_sell_as_is_value)}</Title>
            </Card>
            <Card withBorder padding="sm">
              <Tooltip
                label="Refined Value for items marked Reprocess, plus Sell-As-Is for everything else in the paste - what the whole batch is worth if you follow each item's own recommendation."
                multiline
                w={260}
              >
                <Text size="xs" c="dimmed" tt="uppercase">Total Batch Value (Optimal)</Text>
              </Tooltip>
              <Title order={4}>{isk(result.totals.total_batch_value_optimal)}</Title>
            </Card>
          </SimpleGrid>

          {result.rows.length === 0 ? (
            <HintCard>No items parsed from the paste.</HintCard>
          ) : (
            <DataTable tableId="refining-reprocessing-quote" rowDetail data={result.rows} columns={columns} maxHeight={480} getRowId={(r) => `${r.name}-${r.type_id ?? 'unknown'}`} />
          )}

          {result.mineral_totals.length > 0 && (
            <>
              <Title order={5}>Minerals You'll Get</Title>
              <Text size="xs" c="dimmed">
                Summed across every item above marked "Reprocess" - what you'd actually walk away with.
              </Text>
              <DataTable tableId="refining-reprocessing-quote-2" rowDetail data={result.mineral_totals} columns={mineralColumns} maxHeight={320}
                getRowId={(m) => String(m.type_id)} />
            </>
          )}
        </>
      )}
    </Stack>
  )
}
