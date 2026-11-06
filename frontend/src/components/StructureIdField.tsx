import { useState } from 'react'
import { NumberInput, Select, Stack, UnstyledButton } from '@mantine/core'

interface Props {
  label: string
  value: number | null
  onChange: (value: number | null) => void
  structureNames: Record<string, string | null> | undefined
}

// Picker-first structure-ID field (same shape as LocationPicker): known
// named structures from the tenant's resolved-names cache are the default
// entry, with a hard fallback to raw numeric ID entry. A structure not yet
// in the cache (brand new, or no character has ESI access to it yet) is a
// guaranteed, routine case, not an edge case, so the picker must never be
// the only way in.
export function StructureIdField({ label, value, onChange, structureNames }: Props) {
  const knownOptions = Object.entries(structureNames ?? {})
    .filter((entry): entry is [string, string] => !!entry[1])
    .map(([id, name]) => ({ value: id, label: `${name} (${id})` }))
  const [manualMode, setManualMode] = useState(false)

  return (
    <Stack gap={2}>
      {manualMode ? (
        <NumberInput label={label} value={value ?? ''} min={1} onChange={(v) => onChange(v ? Number(v) : null)} />
      ) : (
        <Select
          label={label}
          data={knownOptions}
          value={value != null ? String(value) : null}
          onChange={(v) => onChange(v ? Number(v) : null)}
          searchable
          nothingFoundMessage={knownOptions.length === 0 ? 'No known structures' : 'No match'}
        />
      )}
      <UnstyledButton onClick={() => setManualMode((m) => !m)} c="accent" fz="xs" td="underline">
        {manualMode ? 'Search instead' : "Don't see it? Enter ID manually"}
      </UnstyledButton>
    </Stack>
  )
}
