import { useState } from 'react'
import { Badge, Collapse, Group, SegmentedControl, Stack, Table, Text, Title, UnstyledButton } from '@mantine/core'

import { productionApi } from '../../api/client'
import type { ReactionBufferRow, ReactionCapacityBlock, ReactionChainingRow } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { isk, qty } from '../../format'
import { formatEveUtc } from './reactionWindowTime'

function recipeKindLabel(kind: string | undefined, key: string): string {
  if (kind === 'alchemy' || key.startsWith('alchemy:')) return 'alchemy'
  return 'normal'
}

function productLabel(typeName: string | null | undefined, typeId: number): string {
  return typeName?.trim() ? typeName : String(typeId)
}

type W2Demand = NonNullable<ReactionCapacityBlock['w2_demand']>

/** Parts of the "own sales (...)" note: where each target's next-window
 * demand came from (docs/REACTION_CAPACITY_PLAN.md D16, D17). */
function w2DemandParts(w2: W2Demand): string[] {
  const parts = [`${w2.sales_types} items${w2.contract_types ? `, ${w2.contract_types} with contract sales` : ''}`]
  if (w2.market_types) parts.push(`${w2.market_types} from market volume`)
  if (w2.no_sales_types) parts.push(`${w2.no_sales_types} without any sales left out`)
  if (w2.fallback_types) parts.push(`${w2.fallback_types} without sales use the backlog`)
  return parts
}

export function ReactionCapacityHeader({ block }: { block: ReactionCapacityBlock }) {
  const formulasCount = block.formulas_to_buy_count ?? 0
  const formulasIsk = block.formulas_to_buy_isk ?? 0
  return (
    <Stack gap={4}>
      <Text size="sm">
        Reactions: {block.slots_used} of {block.slots_available} jobs, window {formatEveUtc(block.window_next)}
      </Text>
      <Group gap="md">
        <Text size="xs" c="dimmed">
          Value of one more slot: {isk(block.slot_shadow_price)}
        </Text>
        <Text size="xs" c="dimmed">
          Component slot-days / cycle: {block.component_slot_days.toFixed(1)}
        </Text>
        {block.w2_demand && (
          <Text size="xs" c="dimmed" data-testid="w2-demand-source">
            Next window demand: {block.w2_demand.source === 'sales'
              ? `own sales (${w2DemandParts(block.w2_demand).join(', ')})`
              : `backlog${block.w2_demand.reason ? ` (${block.w2_demand.reason.replace(/_/g, ' ')})` : ''}`}
          </Text>
        )}
        {formulasCount > 0 && (
          <Text size="xs" c="dimmed" data-testid="formulas-to-buy">
            Formulas to buy: {formulasCount} ({isk(formulasIsk)})
          </Text>
        )}
      </Group>
      {block.warnings.length > 0 && (
        <Text size="xs" c="orange">{block.warnings.join(' · ')}</Text>
      )}
    </Stack>
  )
}

function ChainingTable({ rows }: { rows: ReactionChainingRow[] }) {
  const setChoice = useAction(
    'Reaction chaining choice',
    async ({ typeId, choice }: { typeId: number; choice: 'buy' | 'wait' }) => {
      await productionApi.setReactionChainChoice(typeId, choice)
    },
    [['production', 'plan'], ['production', 'asset-plan'], ['production', 'reaction-chain-choices']],
  )

  if (rows.length === 0) return null
  return (
    <Stack gap="xs" mt="sm">
      <Title order={6} c="dimmed" tt="uppercase">Chaining (buy now vs wait)</Title>
      <Text size="xs" c="dimmed">
        Default is buy. Waiting moves this window&apos;s demand to the next cycle so intermediates can feed it.
        Changing a choice marks the cached plan stale — recompute to apply.
      </Text>
      <Table striped withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Product</Table.Th>
            <Table.Th>ISK saved by waiting</Table.Th>
            <Table.Th>Units delayed</Table.Th>
            <Table.Th>Choice</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr key={row.product_type_id}>
              <Table.Td>{productLabel(row.type_name, row.product_type_id)}</Table.Td>
              <Table.Td>{isk(row.isk_saved_by_waiting)}</Table.Td>
              <Table.Td>{qty(row.units_delayed)}</Table.Td>
              <Table.Td>
                <SegmentedControl
                  size="xs"
                  data-testid={`reaction-chain-choice-${row.product_type_id}`}
                  value={row.preferred}
                  onChange={(v) => {
                    if (v === 'buy' || v === 'wait') {
                      setChoice.mutate({ typeId: row.product_type_id, choice: v })
                    }
                  }}
                  data={[
                    { label: 'Buy now', value: 'buy' },
                    { label: 'Wait', value: 'wait' },
                  ]}
                />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  )
}

function NextWindowPreview({ block }: { block: ReactionCapacityBlock }) {
  const [open, setOpen] = useState(false)
  const nw = block.next_window
  if (!nw) return null
  return (
    <Stack gap={4} mt="sm">
      <UnstyledButton onClick={() => setOpen((o) => !o)}>
        <Text size="sm" c="dimmed">{open ? '▾' : '▸'} Next-window preview (forecast)</Text>
      </UnstyledButton>
      <Collapse expanded={open}>
        <Text size="xs" c="dimmed" mb="xs">
          W2 jobs used: {nw.jobs_used.toFixed(1)}. Purchases and jobs below are a forecast, not this window&apos;s plan.
        </Text>
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Product</Table.Th>
              <Table.Th>Recipe</Table.Th>
              <Table.Th>Jobs</Table.Th>
              <Table.Th>Runs</Table.Th>
              <Table.Th>Runs / job</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {nw.recipes.map((r) => (
              <Table.Tr key={r.recipe_key}>
                <Table.Td>{productLabel(r.type_name, r.product_type_id)}</Table.Td>
                <Table.Td>{recipeKindLabel(r.recipe_kind, r.recipe_key)}</Table.Td>
                <Table.Td>{r.jobs}</Table.Td>
                <Table.Td>{r.runs.toFixed(1)}</Table.Td>
                <Table.Td>{r.runs_per_job.toFixed(1)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Collapse>
    </Stack>
  )
}

/** Overbuild buffer of reaction products (D9 revised): filled with spare slots only. */
export function BufferTable({ rows }: { rows: ReactionBufferRow[] | undefined }) {
  const [open, setOpen] = useState(false)
  if (!rows || rows.length === 0) return null
  const openCount = rows.filter((r) => r.open > 0.5).length
  return (
    <Stack gap={4} mt="sm">
      <UnstyledButton onClick={() => setOpen((o) => !o)}>
        <Text size="sm" c="dimmed" data-testid="reaction-buffer-toggle">
          {open ? '▾' : '▸'} Buffer: {rows.length - openCount} of {rows.length} products filled
          {openCount > 0 ? `, ${openCount} open` : ''}
        </Text>
      </UnstyledButton>
      <Collapse expanded={open}>
        <Text size="xs" c="dimmed" mb="xs">
          The overbuild buffer on reaction products is reacted only with spare slots and is never bought.
          An open buffer stays open until a later window has room for it.
        </Text>
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Product</Table.Th>
              <Table.Th>Buffer</Table.Th>
              <Table.Th>Filled this window</Table.Th>
              <Table.Th>Open</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r) => (
              <Table.Tr key={r.product_type_id}>
                <Table.Td>{productLabel(r.type_name, r.product_type_id)}</Table.Td>
                <Table.Td>{qty(r.target)}</Table.Td>
                <Table.Td>{qty(r.filled)}</Table.Td>
                <Table.Td>{r.open > 0.5 ? qty(r.open) : '–'}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Collapse>
    </Stack>
  )
}

/** Header + chaining + buffer + next-window preview for Build / Asset plan pages. */
export default function ReactionCapacityPanel({ block }: { block: ReactionCapacityBlock | undefined }) {
  if (!block) return null
  return (
    <Stack gap="xs" mb="sm">
      <ReactionCapacityHeader block={block} />
      <ChainingTable rows={block.chaining} />
      <BufferTable rows={block.buffer} />
      <NextWindowPreview block={block} />
    </Stack>
  )
}

export function CapacityReasonBadge({ reason, iskGivenUp }: { reason?: string | null; iskGivenUp?: number | null }) {
  if (!reason) return null
  if (reason === 'formula') {
    return (
      <Badge color="violet" variant="light" title={iskGivenUp != null ? `Formula cost ${isk(iskGivenUp)}` : undefined}>
        formula
      </Badge>
    )
  }
  if (reason === 'capacity') {
    return (
      <Badge color="orange" variant="light" title={iskGivenUp != null ? `ISK given up ${isk(iskGivenUp)}` : undefined}>
        capacity{iskGivenUp != null ? ` (−${isk(iskGivenUp)})` : ''}
      </Badge>
    )
  }
  if (reason === 'inputs') {
    return (
      <Badge
        color="yellow" variant="light"
        title={`Cannot be reacted in time: its inputs are not on hand at the window, or a job needs it at the window${iskGivenUp != null ? ` - ISK given up ${isk(iskGivenUp)}` : ''}`}
      >
        inputs{iskGivenUp != null ? ` (−${isk(iskGivenUp)})` : ''}
      </Badge>
    )
  }
  return null
}
