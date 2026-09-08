import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ActionIcon, Button, Group, Select, SimpleGrid, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'

import { sortingApi } from '../../api/client'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'

export default function SortingSettings() {
  const { data: sources } = useQuery({ queryKey: ['sorting', 'intake-sources'], queryFn: sortingApi.intakeSources })
  const { data: characters } = useQuery({
    queryKey: ['sorting', 'available-characters'], queryFn: sortingApi.availableCharacters,
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
  const [characterName, setCharacterName] = useState<string | null>(null)
  const [hangarFlag, setHangarFlag] = useState<string | null>('Hangar')
  const [label, setLabel] = useState('')

  const characterOptions = (characters?.characters ?? []).map((name) => ({ value: name, label: name }))
  const hangarFlags = hangarOptions?.hangar_division_flags ?? []

  const canAdd = Boolean(hangarFlag) && (sourceKind === 'corp' || Boolean(characterName))

  return (
    <Stack maw={800}>
      <HintCard>
        Each character can have their own personal hangar as a Wareneingang (filtered to that character&apos;s
        assets). A corp division is shared — it is not owned by one character. The overview sums every source
        and still shows which hangar the stack actually sits in.
      </HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Configured intake sources</Title>
      {!sources || sources.sources.length === 0 ? (
        <Text size="sm" c="dimmed">None yet — add a character hangar or a corp division below.</Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Kind</Table.Th>
              <Table.Th>Character</Table.Th>
              <Table.Th>Hangar</Table.Th>
              <Table.Th>Label</Table.Th>
              <Table.Th w={48} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {sources.sources.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td>{s.source_kind === 'character' ? 'Character' : 'Corp'}</Table.Td>
                <Table.Td>{s.character_name ?? '–'}</Table.Td>
                <Table.Td>{s.hangar_flag}</Table.Td>
                <Table.Td>{s.label ?? '–'}</Table.Td>
                <Table.Td>
                  <ActionIcon
                    variant="subtle" color="danger" aria-label="Remove intake source"
                    onClick={() => removeSource.mutate(s.id)} loading={removeSource.isPending}
                  >
                    <IconTrash size={16} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
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
              if (v === 'character' && !hangarFlag) setHangarFlag('Hangar')
            }
          }}
        />
        {sourceKind === 'character' ? (
          <Select
            label="Character"
            placeholder={characterOptions.length === 0 ? 'No registered characters' : 'Select character'}
            data={characterOptions}
            value={characterName}
            onChange={setCharacterName}
            searchable
          />
        ) : (
          <Text size="sm" c="dimmed" mt={28}>No character — this division is shared.</Text>
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
            character_name: sourceKind === 'character' ? characterName : null,
            label: label.trim() || null,
          }, {
            onSuccess: () => {
              setLabel('')
              if (sourceKind === 'character') setCharacterName(null)
            },
          })}
        >
          Add source
        </Button>
      </Group>
    </Stack>
  )
}
