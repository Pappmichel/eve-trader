// Shared components of the Asset Manager pages (docs/ASSET_MANAGER_PLAN.md).
import { Badge, Group, Tooltip } from '@mantine/core'
import type { AssetWantedBy } from '../../api/types'
import { BADGE_LABELS } from './assetQuery'

export function AssetBadges({ badges }: { badges: string[] | undefined }) {
  if (!badges?.length) return null
  return (
    <Group gap={4} wrap="wrap">
      {badges.map((b) => {
        const meta = BADGE_LABELS[b] ?? { label: b, color: 'gray', hint: b }
        return (
          <Tooltip key={b} label={meta.hint} withArrow>
            <Badge size="xs" variant="light" color={meta.color} style={{ flexShrink: 0, overflow: 'visible' }}>{meta.label}</Badge>
          </Tooltip>
        )
      })}
    </Group>
  )
}

export function WantedBy({ wanted }: { wanted: AssetWantedBy[] | undefined }) {
  if (!wanted?.length) return null
  return (
    <Group gap={4} wrap="nowrap">
      {wanted.map((w, i) => (
        <Tooltip key={`${w.tool}-${w.label}-${i}`} withArrow
          label={w.quantity != null ? `${w.label}: ${w.quantity.toLocaleString('en-US')}` : w.label}>
          <Badge size="xs" variant="outline" color="gray">{w.label}</Badge>
        </Tooltip>
      ))}
    </Group>
  )
}
