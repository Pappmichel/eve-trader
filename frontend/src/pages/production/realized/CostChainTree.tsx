import { useState } from 'react'
import { Badge, Group, Stack, Text } from '@mantine/core'
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react'

import type { RealizedChainNode } from '../../../api/types'
import { dateTime, isk, pct, qty } from '../../../format'
import { COLORS } from '../../../theme'
import { FlagMarkers } from './flags'
import { activityLabel, locationLabel } from './labels'

function when(iso: string | null | undefined): string {
  if (!iso) return '–'
  return dateTime(iso)
}

/** Line cost shown on a node. Jobs use the API's cost_share. Leaves use unit cost × the API quantity. */
function chainLineCost(node: RealizedChainNode): number | null {
  if (node.kind === 'job') return node.cost_share
  if (node.unit_cost == null || node.quantity == null) return null
  return node.unit_cost * node.quantity
}

function isEstimateOrigin(origin: string | null): boolean {
  return origin === 'opening' || origin === 'unknown'
}

export function CostChainTree({ node, locationNames, depth = 0 }: {
  node: RealizedChainNode
  locationNames?: Record<string, string | null>
  depth?: number
}) {
  const [open, setOpen] = useState(depth < 2)
  const hasChildren = node.children.length > 0
  const estimate = isEstimateOrigin(node.origin)
  const lineCost = chainLineCost(node)
  const name = node.type_name || (node.type_id != null ? `Type ${node.type_id}` : node.kind)

  return (
    <Stack
      gap={2}
      data-chain-kind={node.kind}
      data-origin={node.origin ?? ''}
      data-depth={depth}
      data-line-cost={lineCost ?? ''}
      data-estimate={estimate ? 'true' : 'false'}
    >
      <Group
        gap={6} wrap="wrap" pl={depth * 16}
        style={{ cursor: hasChildren ? 'pointer' : 'default' }}
        onClick={() => hasChildren && setOpen((value) => !value)}
        role={hasChildren ? 'button' : undefined}
        tabIndex={hasChildren ? 0 : undefined}
        aria-expanded={hasChildren ? open : undefined}
        onKeyDown={(event) => {
          if (!hasChildren) return
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            setOpen((value) => !value)
          }
        }}
      >
        {hasChildren
          ? (open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />)
          : <span style={{ width: 14, flexShrink: 0 }} />}
        <Text size="sm" fw={depth === 0 ? 700 : 500} c={estimate ? 'warn' : undefined} data-estimate={estimate ? 'true' : 'false'}
          style={estimate ? { color: COLORS.warn } : undefined}>
          {name}
        </Text>
        {node.quantity != null && <Text size="xs" c="dimmed">× {qty(node.quantity)}{node.unit ? ` ${node.unit}` : ''}</Text>}
        {node.origin && <Badge size="xs" color={estimate ? 'warn' : 'gray'} variant="light">{node.origin}</Badge>}
        {node.source === 'contract' && <Badge size="xs" variant="light">contract</Badge>}
        {node.kind === 'job' && node.job_id != null && <Text size="xs" c="dimmed">#{node.job_id}</Text>}
        {node.activity_id != null && <Badge size="xs" variant="outline">{activityLabel(node.activity_id)}</Badge>}
        {node.kind === 'job' && node.runs != null && <Text size="xs" c="dimmed">{qty(node.runs)} runs</Text>}
        {node.kind === 'job' && <Text size="xs" c="dimmed">{when(node.date)}{node.end_date ? ` – ${when(node.end_date)}` : ''}</Text>}
        {node.kind === 'job' && node.job_cost != null && <Text size="xs">Job cost {isk(node.job_cost)}</Text>}
        {node.kind === 'job' && (
          <Text size="xs" c="dimmed">Facility {locationLabel(node.facility_id, locationNames)}</Text>
        )}
        {node.share != null && <Text size="xs" data-share={node.share}>Share {pct(node.share)}</Text>}
        {node.cost_share != null && <Text size="xs" data-cost-share={node.cost_share}>Cost share {isk(node.cost_share)}</Text>}
        {node.origin === 'bought' && (
          <>
            <Text size="xs" c="dimmed">{when(node.date)}</Text>
            <Text size="xs" c="dimmed">{locationLabel(node.location_id, locationNames)}</Text>
            <Text size="xs">Price {node.unit_price == null ? '–' : isk(node.unit_price)}</Text>
            <Text size="xs" data-freight-label={node.freight_label ?? ''}>
              Freight {isk(node.freight)}{node.freight_label ? ` (${node.freight_label})` : ''}
            </Text>
          </>
        )}
        {estimate && (
          <Text size="xs" c="warn" data-reference-price={node.reference_price ?? ''}>
            Valued at {node.reference_price == null ? '–' : isk(node.reference_price)}
          </Text>
        )}
        {node.kind !== 'job' && node.kind !== 'product' && node.unit_cost != null && (
          <Text size="xs">Unit cost {isk(node.unit_cost)}</Text>
        )}
        {lineCost != null && <Text size="xs" data-line-cost={lineCost}>Line cost {isk(lineCost)}</Text>}
        <FlagMarkers flags={node.flags} job={node.kind === 'job'} />
      </Group>
      {hasChildren && open && (
        <Stack gap={2}>
          {node.children.map((child, index) => (
            <CostChainTree
              key={`${child.kind}-${child.job_ref ?? child.source_ref ?? child.type_id ?? index}-${depth}-${index}`}
              node={child}
              locationNames={locationNames}
              depth={depth + 1}
            />
          ))}
        </Stack>
      )}
    </Stack>
  )
}
