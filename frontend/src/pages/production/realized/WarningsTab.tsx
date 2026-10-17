import { useEffect, useRef, useState } from 'react'
import { Accordion, Button, Group, Stack, Text } from '@mantine/core'

import type { RealizedWarningGroup } from '../../../api/types'
import { HintCard } from '../../../components/HintCard'
import { warningTarget, warningTitle, type WarningFocus } from './labels'

function formatValue(value: unknown): string {
  if (value == null || value === '') return '–'
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString('en-US') : String(value)
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function WarningItem({ kind, item, onOpen }: {
  kind: string
  item: Record<string, unknown>
  onOpen: (focus: WarningFocus) => void
}) {
  const target = warningTarget(kind, item)
  const fields = Object.entries(item)
  return (
    <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
      <Stack gap={2}>
        {typeof item.message === 'string' && <Text size="sm">{item.message}</Text>}
        {fields.filter(([key]) => key !== 'message').map(([key, value]) => (
          <Text key={key} size="xs" c="dimmed">{key}: {formatValue(value)}</Text>
        ))}
        {fields.length === 0 && <Text size="sm">{kind}</Text>}
      </Stack>
      {target && (
        <Button size="compact-xs" variant="subtle" onClick={() => onOpen(target)}>
          Open
        </Button>
      )}
    </Group>
  )
}

export function WarningsTab({ groups, needsRebuild, hint, onOpen }: {
  groups: RealizedWarningGroup[]
  needsRebuild: boolean
  hint: string
  onOpen: (focus: WarningFocus) => void
}) {
  const openAtStart = groups.filter((group) => group.count > 0).map((group) => group.kind)
  const [opened, setOpened] = useState<string[]>(openAtStart)
  const seeded = useRef(groups.length > 0)
  useEffect(() => {
    if (seeded.current || groups.length === 0) return
    seeded.current = true
    setOpened(groups.filter((group) => group.count > 0).map((group) => group.kind))
  }, [groups])
  if (needsRebuild) return <HintCard>{hint}</HintCard>
  if (groups.length === 0) return <HintCard>No warnings.</HintCard>
  return (
    <Accordion multiple value={opened} onChange={setOpened}>
      {groups.map((group) => (
        <Accordion.Item key={group.kind} value={group.kind}>
          <Accordion.Control>
            {warningTitle(group.kind)} ({group.count})
          </Accordion.Control>
          <Accordion.Panel keepMounted={false}>
            <Stack gap="sm">
              {group.items.length === 0 && <Text size="sm" c="dimmed">None.</Text>}
              {group.items.map((item, index) => (
                <WarningItem key={`${group.kind}-${index}`} kind={group.kind} item={item} onOpen={onOpen} />
              ))}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      ))}
    </Accordion>
  )
}
