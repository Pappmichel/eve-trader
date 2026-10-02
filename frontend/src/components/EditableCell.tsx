import { useState } from 'react'
import { ActionIcon, Group, NumberInput, Tooltip } from '@mantine/core'
import { IconAlertTriangle, IconCheck } from '@tabler/icons-react'

// Inline cell editor (GitHub issue #16 - "should be able to change the
// targets directly in the table, like the targets on the doctrine table")
// - same "local draft state, checkmark appears once it differs from the
// saved value, click to save" pattern as doctrine/DoctrineDetail.tsx's own
// TargetEditor. Safe to key state purely off the initial `value` prop
// (no resync effect needed) for the same reason that component doesn't need
// one either: a successful save's own draft value becomes the next `value`
// this cell receives, so draft and value naturally converge without one -
// see DataTable's `getRowId` prop (used below) for what actually *would*
// break this if it were missing: without a stable per-row identity, sorting
// or filtering could hand this same mounted component a *different* row's
// data on cell state, silently saving one item's edit against another's
// type_id (the exact bug StockTargets.tsx's old CurrentStockInput hit once,
// documented in that component's own history).
export function EditableNumberCell({ value, ariaLabel, isPending, onSave, flagged, min = 0, max, step, width = 90 }: {
  value: number
  ariaLabel: string
  isPending: boolean
  onSave: (value: number) => void
  flagged?: boolean
  // Bounds/step of the field; an emptied field falls back to `min`.
  min?: number
  max?: number
  step?: number
  width?: number
}) {
  const [draft, setDraft] = useState(value)
  const dirty = draft !== value
  // Enter saves (only when changed), Escape discards the draft. Deliberately no
  // save-on-blur: that fired one save per keystroke/spinner click once (see the
  // Shortlist cap draft), so saving stays an explicit action.
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && dirty) onSave(draft)
    if (e.key === 'Escape') setDraft(value)
  }
  return (
    <Group gap={4} wrap="nowrap">
      <NumberInput
        value={draft}
        onChange={(v) => setDraft(v === '' ? min : Number(v))}
        min={min}
        max={max}
        step={step}
        size="xs"
        w={width}
        aria-label={ariaLabel}
        onKeyDown={onKeyDown}
        styles={flagged ? { input: { borderColor: 'var(--mantine-color-danger-5)' } } : undefined}
      />
      {flagged && !dirty && (
        <Tooltip label="Far larger than every target set for this item - likely a stray value. Edit and save to fix.">
          <IconAlertTriangle size={14} color="var(--mantine-color-danger-5)" />
        </Tooltip>
      )}
      {dirty && (
        <ActionIcon size="sm" variant="filled" color="accent" aria-label={`Save ${ariaLabel}`}
          onClick={() => onSave(draft)} loading={isPending}>
          <IconCheck size={14} />
        </ActionIcon>
      )}
    </Group>
  )
}
