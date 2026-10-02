import { Text } from '@mantine/core'
import { COLORS } from '../theme'

type SparklineDirection = 'up' | 'down' | 'flat'

function sparklineDirection(values: number[]): SparklineDirection {
  const first = values[0]
  const last = values[values.length - 1]
  if (last > first) return 'up'
  if (last < first) return 'down'
  return 'flat'
}

const DIRECTION_COLOR: Record<SparklineDirection, string> = {
  up: COLORS.accent,
  down: COLORS.danger,
  flat: COLORS.textDim,
}

interface SparklineProps {
  values: number[]
  width?: number
  height?: number
}

// Tiny inline-SVG trend line, colored by first-vs-last direction. Fewer than two
// points cannot show a trend, so it renders a dash instead of a flat line.
export function Sparkline({ values, width = 80, height = 24 }: SparklineProps) {
  if (values.length < 2) return <Text size="sm" c="dimmed">–</Text>
  const direction = sparklineDirection(values)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const pad = 2
  const span = max - min
  const points = values
    .map((v, i) => {
      const x = pad + (i / (values.length - 1)) * (width - 2 * pad)
      const y = span === 0 ? height / 2 : pad + (1 - (v - min) / span) * (height - 2 * pad)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  return (
    <svg
      width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`30 day trend: ${direction}`}
      data-testid="sparkline" data-direction={direction} style={{ display: 'block' }}
    >
      <polyline points={points} fill="none" stroke={DIRECTION_COLOR[direction]} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
