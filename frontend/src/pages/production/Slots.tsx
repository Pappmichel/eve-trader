import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Text, Stack } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { CharacterSlotRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

export default function Slots() {
  const { data, isLoading } = useQuery({ queryKey: ['production', 'slots'], queryFn: productionApi.slots })

  const columns = useMemo<ColumnDef<CharacterSlotRow, any>[]>(() => [
    { header: 'Character', accessorKey: 'character_name', size: 200 },
    { header: 'Job Type', accessorKey: 'job_type', size: 150 },
    { header: 'Total Slots', accessorKey: 'total_slots', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Used', accessorKey: 'used_slots', size: 90, cell: (i) => qty(i.getValue()) },
    { header: 'Free', accessorKey: 'free_slots', size: 90, cell: (i) => qty(i.getValue()) },
  ], [])

  if (isLoading) return <DataTable data={[]} columns={columns} isLoading maxHeight={400} />
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
      <DataTable data={data} columns={columns} maxHeight={400} />
      <Text size="xs" c="dimmed">
        Manufacturing: Mass Production + Advanced Mass Production. Reactions: Mass Reactions + Advanced Mass Reactions.
        Science (ME/TE research, copying, invention): Laboratory Operation + Advanced Laboratory Operation.
        +1 slot per skill level, base 1.
      </Text>
    </Stack>
  )
}
