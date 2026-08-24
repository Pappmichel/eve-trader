import { useMemo, useState } from 'react'
import { Alert, Badge, Button, Card, Group, SimpleGrid, Stack, Text, Textarea, Title } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { refiningApi } from '../../api/client'
import type { ReprocessingQuoteResult, ReprocessingQuoteRow } from '../../api/types'
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
  const quote = useAction('Get Quote', (text: string) => refiningApi.quoteReprocessing(text))

  const columns = useMemo<ColumnDef<ReprocessingQuoteRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'name', size: 220 },
    { header: 'Qty', accessorKey: 'quantity', size: 90, cell: (i) => qty(i.getValue()) },
    { header: 'Category', accessorKey: 'category', size: 120, meta: { mobileHide: true } },
    {
      header: 'Recommendation', accessorKey: 'decision', size: 150,
      cell: (i) => <Badge color={DECISION_COLOR[i.getValue() as string] ?? 'gray'} variant="light">{i.getValue()}</Badge>,
    },
    { header: 'Sell As-Is (C-J)', accessorKey: 'sell_as_is_value', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Refined Value (C-J)', accessorKey: 'refined_value', size: 150, cell: (i) => isk(i.getValue()) },
    { header: 'Refining Tax', accessorKey: 'refining_tax', size: 110, cell: (i) => isk(i.getValue()), meta: { mobileHide: true } },
    {
      header: 'Note', accessorKey: 'error', size: 200, meta: { mobileHide: true },
      cell: (i) => (i.getValue() ? <Text size="xs" c="dimmed">{i.getValue() as string}</Text> : null),
    },
  ], [])

  return (
    <Stack>
      <HintCard>
        Paste from an EVE Inventory window's list view (Ctrl+A, Ctrl+C) - primarily for ratting loot (T1/meta
        modules, ammo, drones). Uses the scrapmetal reprocessing path (fixed 50% + Scrapmetal Processing skill
        only - structure/rig/implant/general Reprocessing skills don't apply here, see Settings). Nothing is
        auto-decided - both numbers are always shown, you act on the recommendation manually in-game.
      </HintCard>

      <Textarea
        label="Paste items here" placeholder={'Antimatter Charge S\t1000\tProjectile Ammo\tCharge\t\t\t5.0 m3\t\t'}
        autosize minRows={6} maxRows={16} value={paste} onChange={(e) => setPaste(e.currentTarget.value)}
        styles={{ input: { fontFamily: 'monospace' } }}
      />

      <Group>
        <Button loading={quote.isPending} disabled={!paste.trim()}
          onClick={() => quote.mutate(paste, { onSuccess: (r) => setResult(r) })}>
          Get Quote
        </Button>
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
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
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
              <Text size="xs" c="dimmed" tt="uppercase">Total Sell As-Is Value</Text>
              <Title order={4}>{isk(result.totals.total_sell_as_is_value)}</Title>
            </Card>
          </SimpleGrid>

          {result.rows.length === 0 ? (
            <HintCard>No items parsed from the paste.</HintCard>
          ) : (
            <DataTable data={result.rows} columns={columns} maxHeight={480} getRowId={(r) => `${r.name}-${r.type_id ?? 'unknown'}`} />
          )}
        </>
      )}
    </Stack>
  )
}
