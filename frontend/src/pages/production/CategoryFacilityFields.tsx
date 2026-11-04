import { Group, Select, Text } from '@mantine/core'

import type { CategoryFacilityRow, CategoryFacilityUpdate, FacilitySource } from '../../api/types'

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
    </>
  )
}
