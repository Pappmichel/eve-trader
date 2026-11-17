import { Button, Group, Stack, Text } from '@mantine/core'

// A figure the app measured from real data, shown under the setting it would
// apply to with a "Use this value" button. Nothing here changes a setting on
// its own: the click goes through the page's normal save (`onApply`), so
// validation and the cached-plan invalidation stay in one place. Shared by the
// sell-fee hint and the freight hint; each passes its own formatter and texts.
//
// `value` null means there is no trustworthy basis: only `emptyText` shows,
// with the reason, and no button.
export interface ObservedRateHintProps {
  // "Actual last 30 days"
  label: string
  // The suggested setting value, or null when there is none.
  value: number | null
  // What the field holds now; an identical value offers no button.
  current: number
  format: (v: number) => string
  // True when `a` and `b` are the same setting value (the server's rounding).
  same: (a: number, b: number) => boolean
  // "25 sales"
  sample: string
  // Appended to the headline, e.g. " - fees 5.37 %".
  headlineExtra?: string
  // Dimmed explanation under a value.
  detail?: string
  // Shown after "no value (" ... ")" when value is null, plus the message.
  emptyText: string
  emptyMessage?: string | null
  onApply: (value: number) => void
  applying?: boolean
  testId: string
}

export function ObservedRateHint(p: ObservedRateHintProps) {
  if (p.value == null) {
    return (
      <Stack gap={2} data-testid={p.testId}>
        <Text size="xs" c="dimmed">
          {p.label}: no value ({p.emptyText}). {p.emptyMessage}
        </Text>
      </Stack>
    )
  }
  const value = p.value
  return (
    <Stack gap={2} data-testid={p.testId}>
      <Group gap="xs" wrap="nowrap">
        <Text size="xs">
          {p.label}: {p.format(value)} ({p.sample}){p.headlineExtra ?? ''}
        </Text>
        <Button size="compact-xs" variant="light" loading={p.applying}
          disabled={p.same(value, p.current)}
          onClick={() => p.onApply(value)}>
          Use this value
        </Button>
      </Group>
      {p.detail && <Text size="xs" c="dimmed">{p.detail}</Text>}
    </Stack>
  )
}
