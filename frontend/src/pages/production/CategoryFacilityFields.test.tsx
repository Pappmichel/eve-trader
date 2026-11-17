import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it, vi } from 'vitest'

import type { CategoryFacilityRow, CategoryFacilityTax } from '../../api/types'
import { CategoryFacilityFields } from './CategoryFacilityFields'

// Mantine's Select labels both its input and its option list; the input is the one to click.
const selectInput = (name: string) =>
  screen.getAllByLabelText(name).find((e) => e.tagName === 'INPUT') as HTMLElement
const findSelectInput = async (name: string) =>
  (await screen.findAllByLabelText(name)).find((e) => e.tagName === 'INPUT') as HTMLElement

function row(tax: Partial<CategoryFacilityTax> = {}, savedTax: number | null = null): CategoryFacilityRow {
  return {
    category: 'Large Ships', profile: 'manufacturing', location_id: 1049588174021,
    structure_name: 'IAK-JW - CEZ', security: -0.5, split_by_tech: false,
    saved: { structure_type: null, rig_tier_basic: null, rig_tier_advanced: null, facility_tax_rate: savedTax },
    from_name: { rig_tier_basic: null, rig_tier_advanced: null },
    tax: { saved: savedTax, observed: null, effective: 0.0025, source: 'settings', settings_rate: 0.0025, ...tax },
    effective: {
      structure_type: 'Raitaru', structure_source: 'global', rig_tier_basic: 'T1-Rig', rig_source_basic: 'global',
      rig_tier_advanced: null, rig_source_advanced: null, security_multiplier: 1,
    },
  }
}

function renderFields(r: CategoryFacilityRow, onSave = vi.fn(), rigTiers = ['T1-Rig']) {
  render(
    <MantineProvider>
      <CategoryFacilityFields row={r} structureTypes={['Raitaru']} rigTiers={rigTiers} saving={false} onSave={onSave} />
    </MantineProvider>,
  )
  return onSave
}

describe('CategoryFacilityFields facility tax', () => {
  it('shows the observed rate with its job count and age', () => {
    renderFields(row({
      source: 'observed', effective: 0.005,
      observed: { rate: 0.005, observed_at: '2026-11-05T10:00:00Z', age_days: 2.4, jobs: 3 },
    }))
    expect(screen.getByText(
      'Facility tax in use: 0.50% (observed on your last jobs: 0.50% from 3 jobs, newest 2 days ago)',
    )).toBeInTheDocument()
  })

  it('names the Settings page when nothing was observed', () => {
    renderFields(row())
    expect(screen.getByText(/0\.25% \(Settings page, no recent job of yours/)).toBeInTheDocument()
  })

  it('keeps showing what was seen while an override wins', () => {
    renderFields(row({
      source: 'override', effective: 0.02, saved: 0.02,
      observed: { rate: 0.005, observed_at: '2026-11-05T10:00:00Z', age_days: 0.2, jobs: 1 },
    }, 0.02))
    expect(screen.getByText(
      'Facility tax in use: 2.00% (override set here). Last seen: 0.50% from 1 job, newest today',
    )).toBeInTheDocument()
    expect(screen.getByLabelText('Facility tax override')).toHaveValue('2%')
  })

  it('saves the typed override as a fraction, keeping the other saved fields', async () => {
    const onSave = renderFields(row())
    const input = screen.getByLabelText('Facility tax override')
    await userEvent.type(input, '1.5')
    await userEvent.tab()
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith({
      category: 'Large Ships', structure_type: null, rig_tier_basic: null, rig_tier_advanced: null,
      facility_tax_rate: 0.015,
    })
  })

  it('clears the override when the field is emptied', async () => {
    const onSave = renderFields(row({ source: 'override', effective: 0.02, saved: 0.02 }, 0.02))
    await userEvent.clear(screen.getByLabelText('Facility tax override'))
    await userEvent.tab()
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ facility_tax_rate: null }))
  })

  it('does not save when the field is left unchanged', async () => {
    const onSave = renderFields(row())
    await userEvent.click(screen.getByLabelText('Facility tax override'))
    await userEvent.tab()
    expect(onSave).not.toHaveBeenCalled()
  })
})

describe('CategoryFacilityFields TE rig', () => {
  const withTe = (extra: Partial<CategoryFacilityRow['effective']> = {}, category = 'Large Ships'): CategoryFacilityRow => {
    const base = row()
    return {
      ...base, category,
      effective: {
        ...base.effective, te_rig_tier_basic: 'T1-Rig', te_rig_source_basic: 'same as ME',
        te_rig_tier_advanced: null, te_rig_source_advanced: null, ...extra,
      },
    }
  }

  it('shows the TE tier in use with its source', () => {
    renderFields(withTe())
    expect(screen.getByText(/TE T1-Rig \(same as ME\)/)).toBeInTheDocument()
    expect(selectInput('TE rig')).toHaveValue('Auto: T1-Rig (same as ME)')
  })

  it('saves a chosen TE tier next to the other saved fields', async () => {
    const onSave = renderFields(withTe(), vi.fn(), ['No Rig', 'T1-Rig'])
    await userEvent.click(selectInput('TE rig'))
    await userEvent.click(await screen.findByRole('option', { name: 'No Rig' }))
    expect(onSave).toHaveBeenCalledWith({
      category: 'Large Ships', structure_type: null, rig_tier_basic: null, rig_tier_advanced: null,
      facility_tax_rate: null, te_rig_tier_basic: 'No Rig',
    })
  })

  it('does not offer the Thukker rig for reactions', async () => {
    renderFields(withTe({}, 'Reactions'), vi.fn(), ['T1-Rig', 'Thukker'])
    await userEvent.click(selectInput('TE rig'))
    expect(screen.queryByRole('option', { name: 'Thukker' })).not.toBeInTheDocument()
    expect(await screen.findByRole('option', { name: 'T1-Rig' })).toBeInTheDocument()
  })
})
