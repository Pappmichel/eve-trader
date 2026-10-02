import { Progress, Stack, Text } from '@mantine/core'

import type { PipelineRunProgress } from '../api/types'

// Percent done for a background job's progress, or null when it cannot be
// derived (no batch info yet) - the bar is then shown as an animated,
// indeterminate one.
export function jobProgressPercent(progress: PipelineRunProgress | null | undefined): number | null {
  if (!progress?.batch || !progress.total_batches) return null
  return Math.min(100, Math.max(0, Math.round((progress.batch / progress.total_batches) * 100)))
}

export function JobProgress({ label, progress }: {
  label: string
  progress?: PipelineRunProgress | null
}) {
  const percent = jobProgressPercent(progress)
  return (
    <Stack gap={4}>
      <Text size="xs" c="dimmed">{label}</Text>
      <Progress
        value={percent ?? 100}
        animated
        striped={percent === null}
        size="xs"
        color="accent"
        aria-label={percent === null ? 'Job in progress' : `Job ${percent}% done`}
      />
    </Stack>
  )
}
