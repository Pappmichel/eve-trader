import type { ReactNode } from 'react'
import { Badge, Text, Tooltip } from '@mantine/core'

import type { CharInfoField } from '../api/types'

// One place that says what each non-ok field state means, shared by every
// Character Management page (Character Info, Skills, ...) so a table and its
// detail view never disagree (docs/CHARACTER_MANAGEMENT_PLAN.md).
export function FieldState<T>({ field, children }: {
  field: CharInfoField<T>
  children: (value: T) => ReactNode
}) {
  if (field.state === 'ok' && field.value !== null) return <>{children(field.value)}</>
  if (field.state === 'ok') return <Text size="xs" c="dimmed">–</Text>
  if (field.state === 'not_shared') {
    return (
      <Tooltip label="Not shared with this tool. Tick it on the Characters page." multiline w={240}>
        <Text size="xs" c="dimmed">not shared</Text>
      </Tooltip>
    )
  }
  if (field.state === 'reauth_needed') {
    return (
      <Tooltip label="Shared, but no stored token carries the needed scope. Re-authorize this character on the Characters page." multiline w={260}>
        <Badge size="xs" color="warn" variant="light">re-auth needed</Badge>
      </Tooltip>
    )
  }
  if (field.state === 'not_synced') {
    return (
      <Tooltip label={field.detail ?? 'Not synced yet. Press Refresh.'} multiline w={240}>
        <Text size="xs" c="dimmed">not synced yet</Text>
      </Tooltip>
    )
  }
  return (
    <Tooltip label={field.detail ?? 'ESI request failed'} multiline w={260}>
      <Badge size="xs" color="danger" variant="light">error</Badge>
    </Tooltip>
  )
}
