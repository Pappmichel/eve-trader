import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, SimpleGrid, NumberInput, Switch, Button, Center, Loader } from '@mantine/core'

import { doctrineApi } from '../../api/client'
import type { DoctrineSettings as DoctrineSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { useStructureNameOptions } from '../../hooks/useStaticOptions'
import { HintCard } from '../../components/HintCard'
import { StructureIdField } from '../../components/StructureIdField'

export default function DoctrineSettings() {
  const { data } = useQuery({ queryKey: ['doctrine', 'settings'], queryFn: doctrineApi.settings })
  const { data: structureNames } = useStructureNameOptions()
  const [form, setForm] = useState<DoctrineSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const save = useAction('Save Settings', doctrineApi.updateSettings, [['doctrine', 'settings']])

  if (!form) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof DoctrineSettingsT>(key: K, value: DoctrineSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  return (
    <Stack maw={700}>
      <HintCard>Changes take effect immediately. The scheduled sync interval is configured on the Trading Settings page.</HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Structure &amp; Stockpile</Title>
      <SimpleGrid cols={2}>
        <StructureIdField label="Contract structure ID (blank = use Trading's)" value={form.doctrine_structure_id ?? null}
          onChange={(v) => set('doctrine_structure_id', v)} structureNames={structureNames} />
        <StructureIdField label="Stockpile location ID (blank = same as structure)" value={form.stockpile_location_id ?? null}
          onChange={(v) => set('stockpile_location_id', v)} structureNames={structureNames} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Validation</Title>
      <SimpleGrid cols={2}>
        <NumberInput label="Cargo/drone/charge tolerance (0-1)" value={form.cargo_tolerance_pct} min={0} max={1} step={0.05}
          onChange={(v) => set('cargo_tolerance_pct', Number(v))} />
      </SimpleGrid>
      <Switch label="Treat unexpected extra items as a defect (strict mode)" checked={form.strict_extras}
        onChange={(e) => set('strict_extras', e.currentTarget.checked)} />
      <Text size="xs" c="dimmed">
        Modules/rigs/subsystems always need an exact match. Drones, cargo, and loaded charges only need to clear
        the tolerance above to count as "tolerable" instead of "critical".
      </Text>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Shopping List</Title>
      <SimpleGrid cols={2}>
        <NumberInput label="Jita import cost (ISK/m³)" value={form.import_cost_per_m3} min={0} step={50}
          onChange={(v) => set('import_cost_per_m3', Number(v))} />
      </SimpleGrid>
      <Text size="xs" c="dimmed">
        Only affects the Shopping List's own Buy-Jita price (a separate setting from Production's own freight
        cost, seeded from the same 900 ISK/m³ default) - fitted modules/ships plausibly have different logistics
        than raw production materials.
      </Text>

      <Button mt="md" w={200} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Settings
      </Button>
    </Stack>
  )
}
