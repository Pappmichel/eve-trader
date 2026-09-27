import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, SimpleGrid, NumberInput, Button, Center, Loader } from '@mantine/core'

import { moduleReprocessingApi } from '../../api/client'
import type { ModuleReprocessingSettings as ModuleReprocessingSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { HintCard } from '../../components/HintCard'

export default function ModuleReprocessingSettings() {
  const { data } = useQuery({ queryKey: ['module_reprocessing', 'settings'], queryFn: moduleReprocessingApi.settings })

  const [form, setForm] = useState<ModuleReprocessingSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const save = useAction('Save Settings', moduleReprocessingApi.updateSettings, [['module_reprocessing', 'settings']])

  if (!form) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof ModuleReprocessingSettingsT>(key: K, value: ModuleReprocessingSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  return (
    <Stack maw={800}>
      <HintCard>
        Changes take effect immediately. Structure/rig/security/implant have no effect on module/drone
        reprocessing (only the ore/ice path uses them) - the only yield-relevant field here is the
        Scrapmetal Processing skill level.
      </HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Reprocessing Yield</Title>
      <SimpleGrid cols={2}>
        <NumberInput label="Scrapmetal Processing skill (0-5)" value={form.scrapmetal_processing_skill_level}
          min={0} max={5} step={1} onChange={(v) => set('scrapmetal_processing_skill_level', Number(v))} />
        <NumberInput label="Refining tax (0-1)" value={form.refining_tax_rate} min={0} max={1} step={0.01}
          onChange={(v) => set('refining_tax_rate', Number(v))} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Economy</Title>
      <SimpleGrid cols={2}>
        <NumberInput label="Freight cost per m³" value={form.freight_cost_per_m3} min={0} step={10}
          onChange={(v) => set('freight_cost_per_m3', Number(v))} />
        <NumberInput label="Minimum profit / unit" value={form.min_profit_threshold} min={0} step={100}
          onChange={(v) => set('min_profit_threshold', Number(v))} />
        <NumberInput label="Minimum margin (0-1+)" value={form.min_margin_threshold} min={0} step={0.01}
          onChange={(v) => set('min_margin_threshold', Number(v))} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Purchase Source</Title>
      <SimpleGrid cols={2}>
        <NumberInput label="Purchase region ID (default: Jita/The Forge)" value={form.purchase_region_id} min={1}
          onChange={(v) => set('purchase_region_id', Number(v))} />
      </SimpleGrid>

      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Settings
      </Button>
    </Stack>
  )
}
