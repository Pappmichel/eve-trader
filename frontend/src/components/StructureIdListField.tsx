import { useState } from 'react'
import { ActionIcon, Button, Group, Stack, Text } from '@mantine/core'
import { IconPlus, IconTrash } from '@tabler/icons-react'

import { StructureIdField } from './StructureIdField'

interface Props {
  label: string
  description?: string
  value: number[]
  onChange: (value: number[]) => void
  structureNames: Record<string, string | null> | undefined
}

interface Row {
  key: number
  id: number | null
}

// A list of StructureIdFields (picker with the manual-ID fallback each) for
// settings that take several structures. Empty rows stay on screen while
// being filled in; onChange only ever reports the entered ids.
export function StructureIdListField({ label, description, value, onChange, structureNames }: Props) {
  const [rows, setRows] = useState<Row[]>(() => value.map((id, i) => ({ key: i, id })))
  const [nextKey, setNextKey] = useState(value.length)

  const update = (next: Row[]) => {
    setRows(next)
    onChange(next.map((r) => r.id).filter((id): id is number => id != null))
  }

  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>{label}</Text>
      {description && <Text size="xs" c="dimmed">{description}</Text>}
      {rows.map((row, i) => (
        <Group key={row.key} align="flex-start" gap="xs" wrap="nowrap">
          <div style={{ flex: 1 }}>
            <StructureIdField label={`Structure ${i + 1}`} value={row.id} structureNames={structureNames}
              onChange={(id) => update(rows.map((r) => (r.key === row.key ? { ...r, id } : r)))} />
          </div>
          <ActionIcon variant="subtle" color="danger" mt={26} aria-label="Remove structure"
            onClick={() => update(rows.filter((r) => r.key !== row.key))}>
            <IconTrash size={16} />
          </ActionIcon>
        </Group>
      ))}
      <Button variant="light" size="xs" w={160} leftSection={<IconPlus size={14} />}
        onClick={() => { setRows([...rows, { key: nextKey, id: null }]); setNextKey(nextKey + 1) }}>
        Add structure
      </Button>
    </Stack>
  )
}
