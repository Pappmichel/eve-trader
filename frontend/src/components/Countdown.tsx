import { useEffect, useState } from 'react'
import { Text } from '@mantine/core'

import { duration } from '../format'

// Time left until `until`, re-rendered every 30 s (the finest unit shown is a
// minute). `doneLabel` is shown when the date is missing or already past.
export function Countdown({ until, doneLabel }: { until: string | null | undefined; doneLabel: string }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])
  const target = until ? Date.parse(until) : NaN
  if (Number.isNaN(target) || target <= now) return <Text size="sm" c="dimmed" component="span">{doneLabel}</Text>
  return <Text size="sm" component="span">{duration(Math.round((target - now) / 1000))}</Text>
}
