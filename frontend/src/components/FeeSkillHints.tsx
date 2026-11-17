import { Select, Text } from '@mantine/core'

import type { FeeSkillHints, FeeSkillPart, FeeSkillReason, ObservedBuyFees } from '../api/types'
import { ObservedRateHint } from './ObservedRateHint'

const pct = (v: number) => `${(v * 100).toFixed(3)} %`

// The server rounds `suggested` to 4 decimals: a field already holding it
// offers no button.
const sameValue = (a: number, b: number) => Math.abs(a - b) < 0.00005

const REASON_TEXT: Record<FeeSkillReason, string> = {
  no_character_selected: 'no character chosen',
  not_shared: 'not shared',
  reauth_needed: 're-authorization needed',
  not_synced: 'not synced',
  error: 'read error',
  player_structure: 'player structure',
}

// The one character whose skills (and standings) the fee hints use. Choosing
// one changes no fee: it only selects whose figures the hints below show.
// Offers the characters that share Skills with this tool; a stored choice
// that no longer shares stays selectable so it is not silently dropped.
export function FeeSkillCharacterSelect({ hints, value, onChange, needsStandings = true }: {
  hints: FeeSkillHints | undefined
  value: number | null
  onChange: (characterId: number | null) => void
  needsStandings?: boolean
}) {
  const candidates = hints?.candidates ?? []
  const data = candidates.map((c) => ({
    value: String(c.character_id),
    label: needsStandings && !c.shares_standings ? `${c.character_name} (no standings shared)` : c.character_name,
  }))
  if (value != null && !candidates.some((c) => c.character_id === value)) {
    data.push({ value: String(value), label: `${hints?.character_name ?? `#${value}`} (not sharing Skills)` })
  }
  return (
    <Select
      label="Fee skills character"
      description={needsStandings
        ? 'Whose Accounting, Broker Relations and standings the fee hints use. Choosing one changes no fee.'
        : 'Whose skills the fee hints use. Choosing one changes no fee.'}
      placeholder={candidates.length ? 'None' : 'No character shares Skills with this tool'}
      data={data} value={value == null ? null : String(value)} clearable
      onChange={(v) => onChange(v == null ? null : Number(v))}
      data-testid="fee-skill-character"
    />
  )
}

function standingsDetail(part: FeeSkillPart, hints: FeeSkillHints): string {
  const levels = hints.skill_levels
  const skill = levels ? `Broker Relations ${levels.broker_relations}` : ''
  if (part.upper_bound) return `${skill}. Upper bound: ${part.upper_bound_message ?? ''}`
  const s = part.standings
  if (!s) return skill
  const where = hints.location.name ? ` towards the owner of ${hints.location.name}` : ''
  return `${skill}, raw standings${where}: faction ${s.faction_standing.toFixed(2)}, `
    + `corporation ${s.corporation_standing.toFixed(2)}${s.as_of ? ` (as of ${s.as_of.slice(0, 10)})` : ''}.`
}

// One skill-based fee (sales tax or broker fee) of the chosen character, with
// "Use this value" (the click goes through the page's normal save).
export function SkillFeeHint({ hints, part, kind, label, current, onApply, applying, testId }: {
  hints: FeeSkillHints | undefined
  part: FeeSkillPart | null | undefined
  kind: 'broker' | 'tax'
  label: string
  current: number
  onApply: (value: number) => void
  applying?: boolean
  testId: string
}) {
  if (!hints || !part) return null
  const who = hints.character_name ? ` (${hints.character_name})` : ''
  const detail = kind === 'broker'
    ? standingsDetail(part, hints)
    : hints.skill_levels ? `Accounting ${hints.skill_levels.accounting}: 7.5 % x (1 - 0.11 x level).` : ''
  return (
    <ObservedRateHint
      testId={testId}
      label={`${label}${who}`}
      value={part.suggested}
      current={current}
      format={pct}
      same={sameValue}
      sample={part.upper_bound ? 'upper bound' : part.source === 'skills_standings' ? 'skills + standings' : 'skills'}
      detail={detail}
      emptyText={part.reason ? REASON_TEXT[part.reason] ?? part.reason : 'no basis'}
      emptyMessage={part.message}
      onApply={onApply}
      applying={applying}
    />
  )
}

// What the wallet actually paid in broker fees on hub purchases (Trading's
// buy broker fee, O3-08): instant buys pay none, so this is usually lower than
// a standing buy order's fee.
export function ObservedBuyFeeHint({ fees, current, onApply, applying }: {
  fees: ObservedBuyFees | undefined
  current: number
  onApply: (value: number) => void
  applying?: boolean
}) {
  if (!fees) return null
  return (
    <ObservedRateHint
      testId="observed-buy-fees"
      label={`Actual buys last ${fees.window_days} days`}
      value={fees.suggested}
      current={current}
      format={pct}
      same={sameValue}
      sample={`${fees.buys} buys`}
      detail={'Broker fees / price of market purchases at the hub. Buying from sell orders pays no broker fee. '
        + "In a shared corporation wallet this includes other members' purchases."}
      emptyText={`${fees.buys} of ${fees.min_buys} buys needed`}
      emptyMessage={fees.message}
      onApply={onApply}
      applying={applying}
    />
  )
}

export function FeeSkillNote() {
  return (
    <Text size="xs" c="dimmed">
      Standings are fetched on demand: sync them on the Characters page or Character Info to include them.
    </Text>
  )
}
