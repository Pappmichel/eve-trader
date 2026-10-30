import { Badge, Group, Text, Tooltip } from '@mantine/core'

import { qty } from '../format'

// Warning badges on the Trading/Production Unlisted Stock pages: listing this
// item would take it from a doctrine stockpile that is short of it
// (doctrine.engine.doctrine_needs_by_type) or from Production, which still has
// it on the last Build List's buy list (production.engine.production_needs_by_type).
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

export function ProductionNeedBadge({ needed }: { needed: number | null | undefined }) {
  if (!needed) return null
  return (
    <Tooltip
      label={`On the last Build List's buy list: Production still has to buy ${qty(needed)}. Check before listing it.`}
      multiline w={280}
    >
      <Badge size="xs" color="info" variant="light">Production needs {qty(needed)}</Badge>
    </Tooltip>
  )
}

export function ItemWithNeeds({ name, shortfall, doctrines, productionNeeded }: {
  name: string
  shortfall: number | null | undefined
  doctrines: string[] | undefined
  productionNeeded: number | null | undefined
}) {
  return (
    <Group gap={6} wrap="nowrap">
      <Text size="sm" truncate>{name}</Text>
      <DoctrineNeedBadge shortfall={shortfall} doctrines={doctrines} />
      <ProductionNeedBadge needed={productionNeeded} />
    </Group>
  )
}
