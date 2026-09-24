import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useDebouncedValue } from '@mantine/hooks'
import { Button, Group, NumberInput, Select, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'

import { productionApi } from '../api/client'

interface Props {
  label: string
  value: number | null
  onChange: (value: number | null) => void
  // Shows a "No location" option, mapped to location_id 0 (the manual-
  // tracking tables' own "no location" sentinel - see
  // docs/MANUAL_TRACKING_PLAN.md phase 2/3) instead of null.
  allowNone?: boolean
}

const NONE_VALUE = '__none__'

// The shared location field for manual stock/blueprints/jobs (docs/
// MANUAL_TRACKING_PLAN.md phase 2, wired into those pages in later
// phases): search across NPC stations, this tenant's own resolved
// structures and its own manual names (GET /production/locations/search -
// storage.search_locations), with a manual-ID fallback (same "always allow
// raw numeric entry" reasoning as StructureIdField, which this supersedes
// for manual-tracking's own location fields) that can Resolve a
// not-yet-cached structure via ESI (POST /logistics/resolve-structure-name)
// or, if that fails, give it this tenant's own name (POST
// /production/locations/manual-names) instead of getting stuck.
export function LocationPicker({ label, value, onChange, allowNone = false }: Props) {
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const [manualMode, setManualMode] = useState(false)
  const [manualId, setManualId] = useState<number | null>(value)
  const [resolveState, setResolveState] = useState<'idle' | 'resolving' | 'failed'>('idle')
  const [ownName, setOwnName] = useState('')

  const trimmed = debouncedQuery.trim()
  const { data: results } = useQuery({
    queryKey: ['production', 'locations', 'search', trimmed],
    queryFn: () => productionApi.searchLocations(trimmed),
    enabled: trimmed.length >= 2,
  })

  const options = [
    ...(allowNone ? [{ value: NONE_VALUE, label: 'No location' }] : []),
    ...(results ?? []).map((r) => ({ value: String(r.location_id), label: `${r.name} (${r.location_id})` })),
  ]

  async function handleResolve() {
    if (!manualId) return
    setResolveState('resolving')
    try {
      const res = await productionApi.resolveStructureName(manualId)
      if (res.name) {
        onChange(manualId)
        setResolveState('idle')
      } else {
        setResolveState('failed')
      }
    } catch {
      setResolveState('failed')
    }
  }

  async function handleSaveOwnName() {
    if (!manualId || !ownName.trim()) return
    await productionApi.setManualLocationName(manualId, ownName.trim())
    onChange(manualId)
    setResolveState('idle')
  }

  if (manualMode) {
    return (
      <Stack gap={4}>
        <NumberInput
          label={label}
          value={manualId ?? ''}
          min={1}
          onChange={(v) => {
            setManualId(v ? Number(v) : null)
            setResolveState('idle')
          }}
        />
        <Group gap="xs">
          <Button size="xs" variant="light" onClick={handleResolve} loading={resolveState === 'resolving'} disabled={!manualId}>
            Resolve
          </Button>
          <UnstyledButton onClick={() => setManualMode(false)} c="accent" fz="xs" td="underline">
            Search instead
          </UnstyledButton>
        </Group>
        {resolveState === 'failed' && (
          <Stack gap={4}>
            <Text fz="xs" c="dimmed">Could not resolve that structure - give it your own name instead.</Text>
            <Group gap="xs" align="flex-end">
              <TextInput
                label="Give it your own name"
                value={ownName}
                onChange={(e) => setOwnName(e.currentTarget.value)}
                size="xs"
              />
              <Button size="xs" onClick={handleSaveOwnName} disabled={!ownName.trim()}>
                Save
              </Button>
            </Group>
          </Stack>
        )}
      </Stack>
    )
  }

  return (
    <Stack gap={2}>
      <Select
        label={label}
        placeholder="Search stations, structures, manual names..."
        searchable
        searchValue={query}
        onSearchChange={setQuery}
        data={options}
        filter={({ options: opts }) => opts}
        value={value === 0 && allowNone ? NONE_VALUE : value != null ? String(value) : null}
        onChange={(v) => {
          if (!v) return
          onChange(v === NONE_VALUE ? 0 : Number(v))
        }}
        nothingFoundMessage={trimmed.length < 2 ? 'Type at least 2 characters' : 'No match'}
      />
      <UnstyledButton onClick={() => { setManualMode(true); setManualId(value) }} c="accent" fz="xs" td="underline">
        Don't see it? Enter ID manually
      </UnstyledButton>
    </Stack>
  )
}
