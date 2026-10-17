import { useEffect, useRef, useState } from 'react'
import { Accordion, Button, Group, Stack, Text } from '@mantine/core'

import type { RealizedWarningGroup } from '../../../api/types'
import { HintCard } from '../../../components/HintCard'
import { dateTime, isk } from '../../../format'
import { displayQty, warningTarget, warningTitle, type WarningFocus } from './labels'

function formatValue(value: unknown): string {
  if (value == null || value === '') return '–'
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString('en-US') : String(value)
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

const GROUP_PREVIEW = 50

// Ids the API already resolved into a name (or into `text`) are not listed
// again; an id without a name is shown under the same label instead.
const HIDDEN = new Set([
  'message', 'text', 'type_id', 'type_name', 'owner_id', 'owner_type', 'owner_name',
  'location_id', 'location_name', 'job_id', 'job_ref', 'job_label',
])
const LABELS: Record<string, string> = {
  ledger_qty: 'Ledger qty', esi_qty: 'ESI stock', drift: 'Drift', data_kind: 'Data',
  gap_start: 'Gap from', gap_end: 'Gap to', fee_date: 'Fee day', amount: 'Amount',
  count: 'Purchases', sale_ref: 'Sale',
}

function fieldValue(key: string, value: unknown): string {
  if (typeof value === 'number' && key === 'amount') return isk(value)
  if (typeof value === 'number' && (key === 'ledger_qty' || key === 'esi_qty' || key === 'drift')) return displayQty(value)
  if (typeof value === 'string' && /^(gap_|fee_date)/.test(key)) return dateTime(value)
  if (typeof value === 'string' && key === 'data_kind') return value.replaceAll('_', ' ')
  return formatValue(value)
}

/** Named lines for an item's ids; `text` already names them for message items. */
function namedLines(item: Record<string, unknown>): [string, string][] {
  const lines: [string, string][] = []
  const has = (key: string) => item[key] != null && item[key] !== ''
  if (typeof item.text !== 'string') {
    if (has('type_id')) lines.push(['Item', String(item.type_name ?? `Type ${item.type_id}`)])
    if (has('job_ref')) lines.push(['Job', String(item.job_label ?? item.job_ref)])
  }
  if (has('owner_id')) lines.push(['Owner', String(item.owner_name ?? `${item.owner_type ?? ''} ${item.owner_id}`.trim())])
  if (has('location_id')) lines.push(['Location', String(item.location_name ?? item.location_id)])
  return lines
}

function WarningItem({ kind, item, onOpen }: {
  kind: string
  item: Record<string, unknown>
  onOpen: (focus: WarningFocus) => void
}) {
  const target = warningTarget(kind, item)
  const fields = Object.entries(item).filter(([key]) => !HIDDEN.has(key))
  const headline = typeof item.text === 'string' ? item.text : typeof item.message === 'string' ? item.message : null
  const named = namedLines(item)
  return (
    <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
      <Stack gap={2}>
        {headline && <Text size="sm">{headline}</Text>}
        {named.map(([label, value]) => (
          <Text key={label} size="xs" c="dimmed">{label}: {value}</Text>
        ))}
        {fields.map(([key, value]) => (
          <Text key={key} size="xs" c="dimmed">{LABELS[key] ?? key.replaceAll('_', ' ')}: {fieldValue(key, value)}</Text>
        ))}
        {!headline && named.length === 0 && fields.length === 0 && <Text size="sm">{kind}</Text>}
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
  const [expanded, setExpanded] = useState<string[]>([])
  if (needsRebuild) return <HintCard>{hint}</HintCard>
  if (groups.length === 0) return <HintCard>No warnings.</HintCard>
  return (
    <Accordion multiple value={opened} onChange={setOpened}>
      {groups.map((group) => {
        // A real ledger has thousands of items in a group; show the first
        // ones and render the rest only on request.
        const all = expanded.includes(group.kind)
        const shown = all ? group.items : group.items.slice(0, GROUP_PREVIEW)
        return (
        <Accordion.Item key={group.kind} value={group.kind}>
          <Accordion.Control>
            {warningTitle(group.kind)} ({group.count})
          </Accordion.Control>
          <Accordion.Panel keepMounted={false}>
            <Stack gap="sm">
              {group.items.length === 0 && <Text size="sm" c="dimmed">None.</Text>}
              {shown.map((item, index) => (
                <WarningItem key={`${group.kind}-${index}`} kind={group.kind} item={item} onOpen={onOpen} />
              ))}
              {shown.length < group.items.length && (
                <Button size="compact-xs" variant="subtle" w="fit-content"
                  onClick={() => setExpanded((list) => [...list, group.kind])}>
                  Show all {group.items.length}
                </Button>
              )}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
        )
      })}
    </Accordion>
  )
}
