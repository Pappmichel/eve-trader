import { useEffect, useState } from 'react'
import { Group, NumberInput, Select, Text } from '@mantine/core'

import type { CategoryFacilityRow, CategoryFacilityTax, CategoryFacilityUpdate, FacilitySource } from '../../api/types'

// 2 decimals: real facility taxes are fractions of a percent (0.25%, 0.5%).
const taxPct = (rate: number) => `${(rate * 100).toFixed(2)}%`

function ageText(days: number): string {
  if (days < 1) return 'today'
  const whole = Math.round(days)
  return whole === 1 ? '1 day ago' : `${whole} days ago`
}

// What the facility tax in use is and where it comes from. An observed rate is
// the median of the last jobs at this category's structure and ages: the
// structure owner can change it at any time.
export function facilityTaxSummary(tax: CategoryFacilityTax): string {
  const observed = tax.observed
  const seen = observed
    ? `${taxPct(observed.rate)} from ${observed.jobs} ${observed.jobs === 1 ? 'job' : 'jobs'}, newest ${ageText(observed.age_days)}`
    : null
  if (tax.source === 'override') {
    return `Facility tax in use: ${taxPct(tax.effective)} (override set here)${seen ? `. Last seen: ${seen}` : ''}`
  }
  if (tax.source === 'observed') {
    return `Facility tax in use: ${taxPct(tax.effective)} (observed on your last jobs: ${seen})`
  }
  return `Facility tax in use: ${taxPct(tax.effective)} (Settings page, no recent job of yours at this structure)`
}

const SOURCE_TEXT: Record<FacilitySource, string> = {
  set: 'set here',
  name: 'from structure name',
  global: 'Settings page',
}

const AUTO = ''

function autoLabel(value: string | null, source: FacilitySource | null): string {
  if (!value || !source || source === 'set') return 'Auto'
  return `Auto: ${value} (${SOURCE_TEXT[source]})`
}

// The "Auto" label shows what applies when nothing is saved for the field:
// the tier the structure name states, else the Settings page's profile.
function autoFallback(row: CategoryFacilityRow, field: 'basic' | 'advanced'): [string | null, FacilitySource] {
  const named = field === 'basic' ? row.from_name.rig_tier_basic : row.from_name.rig_tier_advanced
  if (named) return [named, 'name']
  const eff = row.effective
  const source = field === 'basic' ? eff.rig_source_basic : eff.rig_source_advanced
  const value = field === 'basic' ? eff.rig_tier_basic : eff.rig_tier_advanced
  return source === 'set' ? [null, 'global'] : [value, source ?? 'global']
}

export function CategoryFacilityFields({
  row, structureTypes, rigTiers, saving, onSave,
}: {
  row: CategoryFacilityRow
  structureTypes: string[]
  rigTiers: string[]
  saving: boolean
  onSave: (update: CategoryFacilityUpdate) => void
}) {
  const save = (patch: Partial<CategoryFacilityUpdate>) => onSave({ category: row.category, ...row.saved, ...patch })
  const toValue = (v: string | null) => (v ? v : null)
  const savedTax = row.saved.facility_tax_rate
  const [taxDraft, setTaxDraft] = useState<number | string>(savedTax == null ? '' : +(savedTax * 100).toFixed(4))
  useEffect(() => {
    setTaxDraft(savedTax == null ? '' : +(savedTax * 100).toFixed(4))
  }, [savedTax])
  const commitTax = () => {
    const next = taxDraft === '' ? null : Number(taxDraft) / 100
    if (next === null ? savedTax !== null : savedTax === null || Math.abs(next - savedTax) > 1e-9) {
      save({ facility_tax_rate: next })
    }
  }
  const [basicAuto, basicSource] = autoFallback(row, 'basic')
  const [advancedAuto, advancedSource] = autoFallback(row, 'advanced')
  const structureAuto = row.effective.structure_source === 'set' ? 'Auto' : `Auto: ${row.effective.structure_type}`
  return (
    <>
      <Group gap="xs" mt={4} grow align="flex-end">
        <Select
          size="xs" label={row.split_by_tech ? 'Rig, Tech I' : 'ME rig'} disabled={saving}
          data={[{ value: AUTO, label: autoLabel(basicAuto, basicSource) }, ...rigTiers.map((t) => ({ value: t, label: t }))]}
          value={row.saved.rig_tier_basic ?? AUTO}
          onChange={(v) => save({ rig_tier_basic: toValue(v) })}
          allowDeselect={false}
        />
        {row.split_by_tech && (
          <Select
            size="xs" label="Rig, Tech II/III" disabled={saving}
            data={[{ value: AUTO, label: autoLabel(advancedAuto, advancedSource) }, ...rigTiers.map((t) => ({ value: t, label: t }))]}
            value={row.saved.rig_tier_advanced ?? AUTO}
            onChange={(v) => save({ rig_tier_advanced: toValue(v) })}
            allowDeselect={false}
          />
        )}
      </Group>
      <Select
        size="xs" mt={4} label="Structure type" disabled={saving}
        data={[{ value: AUTO, label: structureAuto }, ...structureTypes.map((t) => ({ value: t, label: t }))]}
        value={row.saved.structure_type ?? AUTO}
        onChange={(v) => save({ structure_type: toValue(v) })}
        allowDeselect={false}
      />
      <Text size="xs" c="dimmed" mt={2}>
        In use: {row.effective.rig_tier_basic}
        {row.split_by_tech && row.effective.rig_tier_advanced ? ` / ${row.effective.rig_tier_advanced} (Tech II/III)` : ''}
        {' '}x{row.effective.security_multiplier.toFixed(1)} security
      </Text>
      <NumberInput
        size="xs" mt={4} label="Facility tax override" suffix="%" decimalScale={2} min={0} max={100} step={0.05}
        placeholder="Auto" disabled={saving} value={taxDraft}
        onChange={setTaxDraft} onBlur={commitTax}
      />
      <Text size="xs" c="dimmed" mt={2}>{facilityTaxSummary(row.tax)}</Text>
    </>
  )
}
