import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ActionIcon, Button, Group, Select, SimpleGrid, Stack, Text, TextInput, Title } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { sortingApi } from '../../api/client'
import type { SortingIntakeSource } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'

export default function SortingSettings() {
  const { data: sources, isLoading: sourcesLoading, isError: sourcesError, refetch: refetchSources } = useQuery({
    queryKey: ['sorting', 'intake-sources'], queryFn: sortingApi.intakeSources,
  })
  const { data: characters } = useQuery({
    queryKey: ['sorting', 'available-characters'], queryFn: sortingApi.availableCharacters,
  })
  const { data: corps } = useQuery({
    queryKey: ['sorting', 'available-corps'], queryFn: sortingApi.availableCorps,
  })
  const { data: hangarOptions } = useQuery({
    queryKey: ['sorting', 'hangar-division-options'], queryFn: sortingApi.hangarDivisionOptions,
  })

  const addSource = useAction('Add intake source', sortingApi.addIntakeSource, [['sorting', 'intake-sources']])
  const removeSource = useAction(
    'Remove intake source',
    (id: number) => sortingApi.removeIntakeSource(id),
    [['sorting', 'intake-sources']],
  )

  const [sourceKind, setSourceKind] = useState<'character' | 'corp'>('character')
  const [ownerName, setOwnerName] = useState<string | null>(null)
  const [hangarFlag, setHangarFlag] = useState<string | null>('Hangar')
  const [label, setLabel] = useState('')

  const characterOptions = (characters?.characters ?? []).map((name) => ({ value: name, label: name }))
  const corpOptions = (corps?.corps ?? []).map((name) => ({ value: name, label: name }))
  const hangarFlags = hangarOptions?.hangar_division_flags ?? []

  const canAdd = Boolean(hangarFlag) && Boolean(ownerName)

  const sourceColumns = useMemo<ColumnDef<SortingIntakeSource, unknown>[]>(() => [
    {
      header: 'Kind', id: 'source_kind', size: 120,
      accessorFn: (r) => (r.source_kind === 'character' ? 'Character' : 'Corp'),
    },
    { header: 'Owner', accessorKey: 'owner_name', size: 220, cell: (i) => (i.getValue() as string | null) ?? '–' },
    { header: 'Hangar', accessorKey: 'hangar_flag', size: 140 },
    { header: 'Label', accessorKey: 'label', size: 200, cell: (i) => (i.getValue() as string | null) ?? '–' },
    {
      header: '', id: 'actions', size: 50, enableSorting: false,
      cell: (i) => (
        <ActionIcon
          variant="subtle" color="danger" aria-label={`Remove intake source ${i.row.original.owner_name ?? i.row.original.id}`}
          onClick={() => removeSource.mutate(i.row.original.id)} loading={removeSource.isPending}
        >
          <IconTrash size={16} />
        </ActionIcon>
      ),
    },
  ], [removeSource])

  return (
    <Stack>
      <HintCard>
        Each character can have their own personal hangar as a Wareneingang (filtered to that character&apos;s
        assets). A corp division is filtered to one corp the same way — pick which corp&apos;s hangar to count.
        The overview sums every source and still shows which hangar the stack actually sits in.
      </HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Configured intake sources</Title>
      {sourcesLoading ? (
        <DataTable data={[]} columns={sourceColumns} isLoading maxHeight={320} />
      ) : sourcesError ? (
        <DataTable data={[]} columns={sourceColumns} isError onRetry={() => refetchSources()} maxHeight={320} />
      ) : !sources || sources.sources.length === 0 ? (
        <Text size="sm" c="dimmed">None yet — add a character hangar or a corp division below.</Text>
      ) : (
        <DataTable
          data={sources.sources}
          columns={sourceColumns}
          tableId="sorting-intake-sources"
          exportFilename="sorting-intake-sources"
          getRowId={(r) => String(r.id)}
          maxHeight={320}
          emptyLabel="None yet — add a character hangar or a corp division below."
        />
      )}

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Add source</Title>
      <SimpleGrid cols={2}>
        <Select
          label="Kind"
          data={[
            { value: 'character', label: 'Character hangar' },
            { value: 'corp', label: 'Corp hangar division' },
          ]}
          value={sourceKind}
          onChange={(v) => {
            if (v === 'character' || v === 'corp') {
              setSourceKind(v)
              setOwnerName(null)
              if (v === 'character' && !hangarFlag) setHangarFlag('Hangar')
            }
          }}
        />
        {sourceKind === 'character' ? (
          <Select
            label="Character"
            placeholder={characterOptions.length === 0 ? 'No synced characters' : 'Select character'}
            data={characterOptions}
            value={ownerName}
            onChange={setOwnerName}
            searchable
          />
        ) : (
          <Select
            label="Corp"
            placeholder={corpOptions.length === 0 ? 'No synced corps' : 'Select corp'}
            data={corpOptions}
            value={ownerName}
            onChange={setOwnerName}
            searchable
          />
        )}
        <Select
          label="Hangar division"
          data={hangarFlags}
          value={hangarFlag}
          onChange={setHangarFlag}
        />
        <TextInput
          label="Label (optional)"
          placeholder="Shown on the overview instead of the default name"
          value={label}
          onChange={(e) => setLabel(e.currentTarget.value)}
        />
      </SimpleGrid>
      <Group>
        <Button
          w={200}
          disabled={!canAdd}
          loading={addSource.isPending}
          onClick={() => addSource.mutate({
            source_kind: sourceKind,
            hangar_flag: hangarFlag ?? '',
            owner_name: ownerName,
            label: label.trim() || null,
          }, {
            onSuccess: () => {
              setLabel('')
              setOwnerName(null)
            },
          })}
        >
          Add source
        </Button>
      </Group>
    </Stack>
  )
}
