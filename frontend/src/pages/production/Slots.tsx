import { useMemo, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Text, Stack, Checkbox } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

const JOB_TYPES = ['Manufacturing', 'Reactions', 'Science']

interface PivotedRow {
  character_name: string
  excluded: boolean
  byJobType: Record<string, { total: number; used: number; free: number }>
}

export default function Slots() {
  const { data, isLoading } = useQuery({ queryKey: ['production', 'slots'], queryFn: productionApi.slots })
  // GitHub issue #39 - exclude a character from the shared free-slot pool
  // (and therefore the asset-optimized build list's slot-splitting) without
  // un-registering their ESI sync entirely.
  const setExcluded = useAction(
    'Set Character Excluded',
    (args: { characterName: string; excluded: boolean }) =>
      productionApi.setCharacterSlotExcluded(args.characterName, args.excluded),
    [['production', 'slots']],
  )

  // One row per character (GitHub issue #8 - the flat one-row-per-(character,
  // job_type) shape from the backend is still needed elsewhere (the
  // Bauliste's per-category free-slot pool), so the pivot into one row per
  // character happens here, display-only, rather than changing that shape.
  const rows = useMemo<PivotedRow[]>(() => {
    const byCharacter = new Map<string, PivotedRow>()
    for (const r of data ?? []) {
      let row = byCharacter.get(r.character_name)
      if (!row) {
        row = { character_name: r.character_name, excluded: r.excluded_from_planning, byJobType: {} }
        byCharacter.set(r.character_name, row)
      }
      row.byJobType[r.job_type] = { total: r.total_slots, used: r.used_slots, free: r.free_slots }
    }
    return [...byCharacter.values()]
  }, [data])

  // Excluded characters are shown dimmed, as the old table did with the whole row.
  const dim = (row: PivotedRow, node: ReactNode) => (row.excluded ? <span style={{ opacity: 0.5 }}>{node}</span> : node)
  const slotColumn = (jt: string, kind: 'total' | 'used' | 'free', label: string): ColumnDef<PivotedRow, any> => ({
    header: `${jt} ${label}`,
    id: `${jt}-${kind}`,
    size: 160,
    accessorFn: (r) => r.byJobType[jt]?.[kind] ?? null,
    cell: (i) => dim(i.row.original, i.getValue() === null ? '–' : qty(i.getValue() as number)),
  })
  const columns: ColumnDef<PivotedRow, any>[] = [
    {
      header: 'Character', accessorKey: 'character_name', size: 200,
      cell: (i) => dim(i.row.original, i.row.original.character_name),
    },
    {
      header: 'Excluded', accessorKey: 'excluded', size: 100,
      cell: (i) => (
        <Checkbox
          aria-label={`Exclude ${i.row.original.character_name} from planning`}
          checked={i.row.original.excluded}
          disabled={setExcluded.isPending}
          onChange={(e) => setExcluded.mutate({ characterName: i.row.original.character_name, excluded: e.currentTarget.checked })}
        />
      ),
    },
    ...JOB_TYPES.flatMap((jt) => [
      slotColumn(jt, 'total', 'Total'), slotColumn(jt, 'used', 'Used'), slotColumn(jt, 'free', 'Free'),
    ]),
  ]

  if (isLoading) return <Text c="dimmed">Loading…</Text>
  if (!data || data.length === 0) {
    return (
      <HintCard>No character slot data yet - run <b>Refresh what I need</b> in the side menu.</HintCard>
    )
  }

  return (
    <Stack>
      <DataTable
        data={rows} columns={columns} maxHeight={560}
        getRowId={(r) => r.character_name} exportFilename="character-slots"
      />
      <Text size="xs" c="dimmed">
        Manufacturing: Mass Production + Advanced Mass Production. Reactions: Mass Reactions + Advanced Mass Reactions.
        Science (ME/TE research, copying, invention): Laboratory Operation + Advanced Laboratory Operation.
        +1 slot per skill level, base 1.
      </Text>
      <Text size="xs" c="dimmed">
        Excluded characters' slots don't count toward the shared free-slot pool used by the asset-optimized build
        list's slot-splitting - useful for an alt kept registered for ESI sync/asset visibility but not actually
        meant to run production jobs.
      </Text>
    </Stack>
  )
}
