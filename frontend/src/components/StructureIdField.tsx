import { useState } from 'react'
import { NumberInput, Stack, UnstyledButton } from '@mantine/core'

import { SearchableSelect } from './SearchableSelect'

interface Props {
  label: string
  value: number | null
  onChange: (value: number | null) => void
  structureNames: Record<string, string | null> | undefined
}

// Every structure-ID field needs a hard fallback to raw numeric entry - a
// structure not yet in the tenant's resolved-names cache (brand new, or no
// producer character has ESI access to it yet) is a guaranteed, routine
// case, not an edge case, so the picker must never be the only way in.
// Defaults to manual mode whenever the current value isn't a known/named
// entry in the cache (including while the cache is still loading).
export function StructureIdField({ label, value, onChange, structureNames }: Props) {
  const knownOptions = Object.entries(structureNames ?? {})
    .filter((entry): entry is [string, string] => !!entry[1])
    .map(([id, name]) => ({ value: id, label: `${name} (${id})` }))
  const currentIsKnown = value != null && !!structureNames?.[String(value)]
  const [manualMode, setManualMode] = useState(!currentIsKnown)

  return (
    <Stack gap={2}>
      {manualMode ? (
        <NumberInput label={label} value={value ?? ''} min={1} onChange={(v) => onChange(v ? Number(v) : null)} />
      ) : (
        <SearchableSelect
          label={label}
          data={knownOptions}
          value={value != null ? String(value) : null}
          onChange={(v) => onChange(v ? Number(v) : null)}
        />
      )}
      <UnstyledButton onClick={() => setManualMode((m) => !m)} c="accent" fz="xs" td="underline">
        {manualMode ? 'Pick from known structures instead' : "Don't see it? Enter ID manually"}
      </UnstyledButton>
    </Stack>
  )
}
