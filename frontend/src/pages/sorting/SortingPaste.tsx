import { useState } from 'react'
import { Button, Group, Stack, Text, Textarea, Tooltip } from '@mantine/core'

import { sortingApi } from '../../api/client'
import type { SortingPasteResult } from '../../api/types'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { SortingTable } from './SortingTable'

export default function SortingPaste() {
  const [paste, setPaste] = useState('')
  const [result, setResult] = useState<SortingPasteResult | null>(null)
  const sort = useAction('Sort paste', (text: string) => sortingApi.sortingPaste(text), [],
    { tier: 'live', effect: 'Checks every pasted item against all demand pots. The reprocess check prices live. Nothing is saved.' })

  return (
    <Stack>
      <HintCard>
        Paste from an EVE Inventory window&apos;s list view (Ctrl+A, Ctrl+C) to sort stock without waiting for an
        asset sync. Nothing is saved. Only the intake comes from the paste: Doctrine and Market listing demand
        still use the last synced assets, and Doctrine shows no demand until assets have been synced once.
      </HintCard>

      <Textarea
        label="Paste items here" placeholder={'Antimatter Charge S\t1000\tProjectile Ammo\tCharge\t\t\t5.0 m3\t\t'}
        autosize minRows={6} maxRows={16} value={paste} onChange={(e) => setPaste(e.currentTarget.value)}
        styles={{ input: { fontFamily: 'monospace' } }}
      />

      <Group>
        <Tooltip label={sort.tooltip} disabled={!sort.tooltip} multiline w={280}>
          <Button leftSection={sort.tierIcon} loading={sort.isPending} disabled={!paste.trim()}
            onClick={() => sort.mutate(paste, { onSuccess: (r) => setResult(r) })}>
            Sort paste
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
          {result.reprocess_error && (
            <Text size="xs" c="warn">Reprocess check unavailable: {result.reprocess_error}</Text>
          )}
          {result.errors.length > 0 && (
            <Text size="sm" c="danger">
              {result.errors.length} line(s) could not be parsed: {result.errors.map((e) => e.line).join(', ')}
            </Text>
          )}
          {result.unresolved.length > 0 && (
            <Text size="sm" c="warn">
              {result.unresolved.length} unresolved line(s):{' '}
              {result.unresolved.map((u) => (u.suggestion ? `${u.line} (did you mean "${u.suggestion}"?)` : u.line)).join('; ')}
            </Text>
          )}
          <SortingTable
            rows={result.rows}
            tableId="sorting-paste"
            exportFilename="sorting-paste"
            emptyLabel="No items could be read from this paste."
          />
        </>
      )}
    </Stack>
  )
}
