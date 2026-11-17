import type { ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it, vi } from 'vitest'

import type { FeeSkillHints } from '../api/types'
import { FeeSkillCharacterSelect, SkillFeeHint } from './FeeSkillHints'

const HINTS: FeeSkillHints = {
  tool_key: 'station_trading', character_id: 1, character_name: 'Alice',
  candidates: [
    { character_id: 1, character_name: 'Alice', shares_standings: true },
    { character_id: 2, character_name: 'Bob', shares_standings: false },
  ],
  reason: null, message: null, skills_as_of: null, skill_levels: { accounting: 4, broker_relations: 5 },
  location: { location_id: 60003760, kind: 'npc_station', name: 'Jita IV', corporation_id: 1000035, faction_id: 500001 },
  sales_tax: {
    setting: 'sales_tax_rate', source: 'skills', suggested: 0.042, rate: 0.042, characters: 1,
    upper_bound: false, reason: null, message: null,
  },
  broker_fee: {
    setting: 'broker_fee_rate', source: 'skills', suggested: 0.015, rate: 0.015, characters: 1,
    upper_bound: true, reason: null, message: null, upper_bound_reason: 'not_synced',
    upper_bound_message: 'Standings have not been synced (they are fetched on demand), so they are left out.',
    standings: null,
  },
}

const wrap = (ui: ReactNode) => render(<MantineProvider>{ui}</MantineProvider>)

describe('SkillFeeHint', () => {
  it('shows the sales tax from Accounting and applies it only on click', async () => {
    const onApply = vi.fn()
    wrap(<SkillFeeHint testId="t" kind="tax" hints={HINTS} part={HINTS.sales_tax} label="From skills"
      current={0.075} onApply={onApply} />)
    expect(screen.getByText(/From skills \(Alice\): 4\.200 % \(skills\)/)).toBeInTheDocument()
    expect(screen.getByText(/Accounting 4/)).toBeInTheDocument()
    expect(onApply).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Use this value' }))
    expect(onApply).toHaveBeenCalledExactlyOnceWith(0.042)
  })

  it('labels a broker fee without standings as an upper bound', () => {
    wrap(<SkillFeeHint testId="t" kind="broker" hints={HINTS} part={HINTS.broker_fee} label="From skills"
      current={0.05} onApply={vi.fn()} />)
    expect(screen.getByText(/1\.500 % \(upper bound\)/)).toBeInTheDocument()
    expect(screen.getByText(/Standings have not been synced/)).toBeInTheDocument()
  })

  it('gives the reason and no button when skills are not shared', () => {
    const part = { ...HINTS.sales_tax!, suggested: null, rate: null, reason: 'not_shared' as const,
      message: 'The chosen character does not share this data with this tool.' }
    wrap(<SkillFeeHint testId="t" kind="tax" hints={{ ...HINTS, reason: 'not_shared' }} part={part}
      label="From skills" current={0.075} onApply={vi.fn()} />)
    expect(screen.getByText(/no value \(not shared\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('FeeSkillCharacterSelect', () => {
  it('offers the sharing characters and keeps a stored choice that stopped sharing', async () => {
    const onChange = vi.fn()
    wrap(<FeeSkillCharacterSelect hints={{ ...HINTS, character_name: 'Carol' }} value={3} onChange={onChange} />)
    const input = screen.getByRole('combobox', { name: /Fee skills character/ })
    expect(input).toHaveValue('Carol (not sharing Skills)')
    await userEvent.click(input)
    expect(await screen.findByRole('option', { name: 'Bob (no standings shared)' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('option', { name: 'Alice' }))
    expect(onChange).toHaveBeenCalledWith(1)
  })
})
