import { useMemo, useState } from 'react'
import { Badge, Button, Group, NumberInput, Paper, Stack, Text } from '@mantine/core'
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react'

import { productionApi } from '../../api/client'
import type { MaterialTreeNode } from '../../api/types'
import { HintCard } from '../../components/HintCard'
import { SearchableSelect } from '../../components/SearchableSelect'
import { useAction } from '../../hooks/useAction'
import { useItemNameOptions } from '../../hooks/useStaticOptions'
import { qty } from '../../format'

const ACTIVITY_COLOR: Record<string, string> = {
  Buy: 'gray',
  'Tech I': 'accent',
  Faction: 'info',
  Storyline: 'info',
  Officer: 'info',
  Deadspace: 'info',
  'Tech II': 'warn',
  Reaction: 'danger',
}

function TreeRow({ node, depth }: { node: MaterialTreeNode; depth: number }) {
  // First two levels start expanded (root + its direct materials) - deeper
  // levels start collapsed, since a T2/T3 item's full tree can run to
  // hundreds of leaf minerals and showing it all by default would just be
  // a wall of text instead of something explorable.
  const [open, setOpen] = useState(depth < 2)
  const hasChildren = node.children.length > 0

  return (
    <Stack gap={2}>
      <Group
        gap={6} wrap="nowrap" pl={depth * 20}
        style={{ cursor: hasChildren ? 'pointer' : 'default' }}
        onClick={() => hasChildren && setOpen((o) => !o)}
        role={hasChildren ? 'button' : undefined}
        tabIndex={hasChildren ? 0 : undefined}
        aria-expanded={hasChildren ? open : undefined}
        onKeyDown={(e) => {
          if (!hasChildren) return
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setOpen((o) => !o)
          }
        }}
      >
        {hasChildren
          ? (open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />)
          : <div style={{ width: 14, flexShrink: 0 }} />}
        <Text size="sm" fw={depth === 0 ? 700 : 400}>{node.type_name}</Text>
        <Text size="xs" c="dimmed">× {qty(node.quantity)}</Text>
        <Badge size="xs" color={ACTIVITY_COLOR[node.activity] ?? 'gray'} variant="light">{node.activity}</Badge>
        {node.decryptor && <Badge size="xs" color="info" variant="outline">{node.decryptor}</Badge>}
      </Group>
      {hasChildren && open && (
        <Stack gap={2}>
          {node.children.map((child, i) => (
            <TreeRow key={`${child.type_id}-${depth}-${i}`} node={child} depth={depth + 1} />
          ))}
        </Stack>
      )}
    </Stack>
  )
}

export default function MaterialTree() {
  const { data: itemNameOptions } = useItemNameOptions()
  const options = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [typeId, setTypeId] = useState<string | null>(null)
  const [quantity, setQuantity] = useState<number | ''>(1)
  const [tree, setTree] = useState<MaterialTreeNode | null>(null)
  const build = useAction('Build Material Tree',
    (args: { typeName: string; quantity: number }) => productionApi.materialTree(args.typeName, args.quantity))

  return (
    <Stack>
      <HintCard>
        Shows the full recursive material recipe for one item - Invention → Components → ... → base minerals -
        exactly as the blueprint defines it, regardless of whether building it is currently economical right now
        (that's what the Build List/Buy List already decide - this is for exploring a recipe, not planning a
        shopping list). Click a row with an arrow to expand/collapse its own materials.
      </HintCard>

      <Group align="flex-end">
        <SearchableSelect label="Item name" placeholder="Search item…" data={options} value={typeId} onChange={setTypeId} w={320} />
        <NumberInput
          label="Quantity" value={quantity} onChange={(v) => setQuantity(v === '' ? '' : Number(v))}
          min={1} w={140}
        />
        <Button
          loading={build.isPending} disabled={!typeId || quantity === ''}
          onClick={() => build.mutate(
            { typeName: options.find((o) => o.value === typeId)?.label ?? '', quantity: Number(quantity) },
            { onSuccess: (r) => setTree(r) },
          )}
        >
          Build Tree
        </Button>
      </Group>

      {tree && (
        <Paper withBorder p="md">
          <TreeRow node={tree} depth={0} />
        </Paper>
      )}
    </Stack>
  )
}
