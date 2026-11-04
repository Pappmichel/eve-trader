import { Alert, Badge, Group, Text, Tooltip } from '@mantine/core'

import { qty } from '../../format'

// Shared by both mineral shopping lists (Ore & Minerals, Module Reprocessing):
// the optimizer only buys what sellers list within the price tolerance.

export function SourceBadge({ source, depthChecked }: { source: string | null; depthChecked: boolean }) {
  return (
    <Group gap={4} wrap="nowrap">
      <Badge size="sm" variant="light" color={source === 'Home' ? 'accent' : 'gray'}>{source ?? '–'}</Badge>
      {!depthChecked && (
        <Tooltip label="Home-market quote: no listed volume available, quantity not checked" multiline w={240}>
          <Badge size="sm" variant="outline" color="warn">qty unchecked</Badge>
        </Tooltip>
      )}
    </Group>
  )
}

export function ShortCell({ value }: { value: number }) {
  return value > 0 ? <Text size="sm" c="danger" fw={600}>{qty(value)}</Text> : <>–</>
}

export function ShortfallAlert({ show, tolerance }: { show: boolean; tolerance: number | undefined }) {
  if (!show) return null
  const pct = tolerance != null ? `${(tolerance * 100).toFixed(1).replace(/\.0$/, '')}%` : 'the tolerance'
  return (
    <Alert color="danger" variant="light" title="Not everything is available at an acceptable price">
      Sellers do not list enough within {pct} of the reference price, even after switching to other sources.
      See the Short column in the Coverage Check.
    </Alert>
  )
}

export const DEPTH_HINT =
  'Quantities are capped at what sellers list within the price tolerance (Settings); deeper orders cost more and ' +
  'are used only while they still beat every other source.'
