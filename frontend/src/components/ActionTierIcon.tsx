import { ThemeIcon } from '@mantine/core'
import { IconCloud, IconDatabase } from '@tabler/icons-react'

// The three tiers a research pass (2026-09-13) found already exist across
// every action button in the app, just with no consistent signal for any of
// them: 'local' never leaves Postgres, 'cached' reads a short-lived cache
// (jita_price_cache, ESIClient's own class-level order-book cache, etc.) and
// only calls out live on a miss, 'live' always hits ESI/Goonmetrics fresh.
export type ActionNetworkTier = 'local' | 'cached' | 'live'

export const TIER_COPY: Record<ActionNetworkTier, string> = {
  local: 'Liest/schreibt nur lokal gespeicherte Daten - kein Aufruf an EVEs Server.',
  cached: 'Nutzt einen kurz zwischengespeicherten Wert und ruft nur bei einem Cache-Miss live bei ESI/Goonmetrics ab.',
  live: 'Ruft live EVEs ESI/Goonmetrics auf - kann mehrere Sekunden dauern und fehlschlagen, wenn der Dienst down ist.',
}

const ICON_BY_TIER: Partial<Record<ActionNetworkTier, { Icon: typeof IconCloud; color: string }>> = {
  live: { Icon: IconCloud, color: 'warn' },
  cached: { Icon: IconDatabase, color: 'info' },
}

// Deliberately renders nothing for 'local' - the whole point is that a live/
// cached call gets a visible-at-a-glance marker, not that every single
// button grows an icon (that would just be new noise for the common case).
export function ActionTierIcon({ tier }: { tier: ActionNetworkTier }) {
  const meta = ICON_BY_TIER[tier]
  if (!meta) return null
  const { Icon, color } = meta
  return (
    <ThemeIcon size={16} radius="xl" variant="light" color={color}>
      <Icon size={11} />
    </ThemeIcon>
  )
}
