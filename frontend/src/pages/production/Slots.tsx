import { Fragment, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Table, Text, Stack } from '@mantine/core'

import { productionApi } from '../../api/client'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

const JOB_TYPES = ['Manufacturing', 'Reactions', 'Science']

interface PivotedRow {
  character_name: string
  byJobType: Record<string, { total: number; used: number; free: number }>
}

export default function Slots() {
  const { data, isLoading } = useQuery({ queryKey: ['production', 'slots'], queryFn: productionApi.slots })

  // One row per character (GitHub issue #8 - the flat one-row-per-(character,
  // job_type) shape from the backend is still needed elsewhere (the
  // Bauliste's per-category free-slot pool), so the pivot into one row per
  // character happens here, display-only, rather than changing that shape.
  const rows = useMemo<PivotedRow[]>(() => {
    const byCharacter = new Map<string, PivotedRow>()
    for (const r of data ?? []) {
      let row = byCharacter.get(r.character_name)
      if (!row) {
        row = { character_name: r.character_name, byJobType: {} }
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
            <Table.Tr key={row.character_name}>
              <Table.Td>{row.character_name}</Table.Td>
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
    </Stack>
  )
}
