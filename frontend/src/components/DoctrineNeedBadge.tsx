import { Badge, Group, Text, Tooltip } from '@mantine/core'

import { qty } from '../format'

// Warning badge on the Trading/Production Unlisted Stock pages: the doctrine
// stockpile is currently short of this item, so listing it would take it from
// a doctrine (doctrine.engine.doctrine_needs_by_type).
export function DoctrineNeedBadge({ shortfall, doctrines }: {
  shortfall: number | null | undefined
  doctrines: string[] | undefined
}) {
  if (!shortfall) return null
  const names = doctrines ?? []
  return (
    <Tooltip
      label={`Doctrine stockpile is short ${qty(shortfall)}${names.length ? ` (${names.join(', ')})` : ''}. Check before listing it.`}
      multiline w={280}
    >
      <Badge size="xs" color="warn" variant="light">Doctrine needs {qty(shortfall)}</Badge>
    </Tooltip>
  )
}

export function ItemWithDoctrineNeed({ name, shortfall, doctrines }: {
  name: string
  shortfall: number | null | undefined
  doctrines: string[] | undefined
}) {
  return (
    <Group gap={6} wrap="nowrap">
      <Text size="sm" truncate>{name}</Text>
      <DoctrineNeedBadge shortfall={shortfall} doctrines={doctrines} />
    </Group>
  )
}
