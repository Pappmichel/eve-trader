import { Box, Text } from '@mantine/core'
import { COLORS } from '../theme'

// Mirrors ui_shell.py's hint_card() - a left-accented callout for empty
// states / guidance text.
export function HintCard({ children }: { children: React.ReactNode }) {
  return (
    <Box
      style={{
        borderLeft: `3px solid ${COLORS.accent}`,
        background: COLORS.surface2,
        padding: '0.85rem 1.1rem',
        borderRadius: 4,
      }}
    >
      <Text size="sm" c="dimmed">
        {children}
      </Text>
    </Box>
  )
}
