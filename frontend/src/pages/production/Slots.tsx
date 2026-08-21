import { Fragment, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Table, Text, Stack, Checkbox } from '@mantine/core'

import { productionApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
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

  if (isLoading) return <Text c="dimmed">Loading…</Text>
  if (!data || data.length === 0) {
    return (
      <HintCard>
        No character slot data - or not synced yet ('Sync ESI Data' in the sidebar). Characters that were added
        before the skills permission existed need to be re-added once.
      </HintCard>
    )
  }

  return (
    <Stack>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th rowSpan={2}>Character</Table.Th>
            <Table.Th rowSpan={2}>Excluded</Table.Th>
            {JOB_TYPES.map((jt) => (
              <Table.Th key={jt} colSpan={3} style={{ textAlign: 'center' }}>{jt}</Table.Th>
            ))}
          </Table.Tr>
          <Table.Tr>
            {JOB_TYPES.map((jt) => (
              <Fragment key={jt}>
                <Table.Th>Total</Table.Th>
                <Table.Th>Used</Table.Th>
                <Table.Th>Free</Table.Th>
              </Fragment>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr key={row.character_name} style={row.excluded ? { opacity: 0.5 } : undefined}>
              <Table.Td>{row.character_name}</Table.Td>
              <Table.Td>
                <Checkbox
                  aria-label={`Exclude ${row.character_name} from planning`}
                  checked={row.excluded}
                  disabled={setExcluded.isPending}
                  onChange={(e) => setExcluded.mutate({ characterName: row.character_name, excluded: e.currentTarget.checked })}
                />
              </Table.Td>
              {JOB_TYPES.map((jt) => {
                const cell = row.byJobType[jt]
                return (
                  <Fragment key={jt}>
                    <Table.Td>{cell ? qty(cell.total) : '–'}</Table.Td>
                    <Table.Td>{cell ? qty(cell.used) : '–'}</Table.Td>
                    <Table.Td>{cell ? qty(cell.free) : '–'}</Table.Td>
                  </Fragment>
                )
              })}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
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
