import { useMemo, useState } from 'react'
import { Button, Group, Stack, Text, TextInput } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { AssetLocationRow, AssetLocationSearchResult } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { qty } from '../../format'

export default function AssetSearch() {
  const [itemName, setItemName] = useState('')
  const [result, setResult] = useState<AssetLocationSearchResult | null>(null)
  const search = useAction('Search', (name: string) => productionApi.searchAssetLocations(name))

  const columns = useMemo<ColumnDef<AssetLocationRow, any>[]>(() => [
    {
      header: 'Location', accessorKey: 'location_name', size: 320,
      cell: (i) => {
        const row = i.row.original
        return row.location_name ? `${row.location_name} (${row.location_id})` : `Structure ID ${row.location_id}`
      },
    },
    { header: 'Owner', accessorKey: 'owner_name', size: 220 },
    { header: 'Quantity', accessorKey: 'quantity', size: 140, cell: (i) => qty(i.getValue()) },
  ], [])

  const total = useMemo(
    () => (result?.locations ?? []).reduce((sum, r) => sum + r.quantity, 0), [result],
  )

  const runSearch = () => {
    if (!itemName.trim()) return
    search.mutate(itemName.trim(), { onSuccess: (r) => setResult(r) })
  }

  return (
    <Stack>
      <HintCard>
        Shows every station/structure a specific item currently sits at, how much, and who it belongs to (which
        character, or "&lt;corp&gt; (corp)" for corp hangar stock) - reflects the last "Sync ESI Data" run, not a
        live lookup. Quantities sitting inside a container, ship, or corp Office are resolved up to the station/
        structure they're actually at.
      </HintCard>

      <Group align="flex-end">
        <TextInput
          label="Item name (exact)" placeholder="e.g. Tritanium" value={itemName}
          onChange={(e) => setItemName(e.currentTarget.value)}
          onKeyDown={(e) => e.key === 'Enter' && runSearch()}
          w={320}
        />
        <Button loading={search.isPending} disabled={!itemName.trim()} onClick={runSearch}>
          Search
        </Button>
      </Group>

      {result && (
        result.locations.length === 0 ? (
          <HintCard>No stock of "{result.type_name}" found anywhere in the last synced assets.</HintCard>
        ) : (
          <Stack>
            <Text size="sm" c="dimmed">
              {result.type_name}: {qty(total)} total across {result.locations.length} location(s)
            </Text>
            <DataTable data={result.locations} columns={columns} maxHeight={480} />
          </Stack>
        )
      )}
    </Stack>
  )
}
