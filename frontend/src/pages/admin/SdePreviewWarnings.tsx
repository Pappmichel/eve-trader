import { Alert, List, Stack, Text } from '@mantine/core'

import type { SdeConstantDrift, SdeShrinkWarning } from '../../api/types'

// Shown above the SDE diff: tables that would shrink a lot (Apply then needs
// its own confirmation) and hard-coded constants that no longer match the
// dump's dogma. Both are advisory; nothing is changed automatically.
export function SdePreviewWarnings({
  shrinkWarnings, drift,
}: { shrinkWarnings: SdeShrinkWarning[]; drift?: SdeConstantDrift }) {
  const drifts = drift?.drifts ?? []
  if (shrinkWarnings.length === 0 && drifts.length === 0) return null
  return (
    <Stack gap="xs">
      {shrinkWarnings.length > 0 && (
        <Alert color="danger" variant="light" title="Tables would shrink a lot">
          <Text size="sm" mb={4}>
            Applying this dump would remove more than 20 % of the rows of these tables. That usually means
            an incomplete download. Re-check first; apply only with the separate confirmation button.
          </Text>
          <List size="sm">
            {shrinkWarnings.map((w) => (
              <List.Item key={w.table}>
                {w.table}: {w.old.toLocaleString('en-US')} → {w.new.toLocaleString('en-US')} (−{w.drop_percent}%)
              </List.Item>
            ))}
          </List>
        </Alert>
      )}
      {drifts.length > 0 && (
        <Alert color="warn" variant="light" title="Constants differ from the dump">
          <Text size="sm" mb={4}>
            These hard-coded values (production/constants.py) no longer match the dogma of this dump. Nothing
            was changed; review them before relying on the numbers.
          </Text>
          <List size="sm">
            {drifts.map((d) => (
              <List.Item key={`${d.constant}-${d.type_id}`}>
                {d.constant}{d.type_name ? ` (${d.type_name})` : ''}: code {d.expected}, dump {d.actual}
              </List.Item>
            ))}
          </List>
        </Alert>
      )}
    </Stack>
  )
}
