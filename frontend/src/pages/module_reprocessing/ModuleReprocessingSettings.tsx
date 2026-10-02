import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, SimpleGrid, NumberInput, Checkbox, Button, Center, Loader } from '@mantine/core'

import { moduleReprocessingApi } from '../../api/client'
import type { ModuleReprocessingSettings as ModuleReprocessingSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { HintCard } from '../../components/HintCard'
import { HubSelect } from '../../components/HubSelect'
import { RegionSelect } from '../../components/RegionSelect'

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
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <NumberInput label="Scrapmetal Processing skill (0-5)" value={form.scrapmetal_processing_skill_level}
          min={0} max={5} step={1} onChange={(v) => set('scrapmetal_processing_skill_level', Number(v))} />
        <NumberInput label="Refining tax" suffix="%" decimalScale={2} value={form.refining_tax_rate * 100}
          min={0} max={100} step={1} onChange={(v) => set('refining_tax_rate', Number(v) / 100)} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Economy</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <NumberInput label="Freight cost per m³" value={form.freight_cost_per_m3} min={0} step={10}
          onChange={(v) => set('freight_cost_per_m3', Number(v))} />
        <NumberInput label="Minimum profit / unit" value={form.min_profit_threshold} min={0} step={100}
          disabled={form.ignore_thresholds}
          onChange={(v) => set('min_profit_threshold', Number(v))} />
        <NumberInput label="Minimum margin" suffix="%" decimalScale={2} value={form.min_margin_threshold * 100}
          min={0} step={1} disabled={form.ignore_thresholds}
          onChange={(v) => set('min_margin_threshold', Number(v) / 100)} />
      </SimpleGrid>
      <Checkbox label="Ignore margin/profit thresholds entirely (auto-add every priced candidate)"
        checked={form.ignore_thresholds}
        onChange={(e) => set('ignore_thresholds', e.currentTarget.checked)} mt={6} />
      <Text size="xs" c="dimmed">
        These two thresholds are what decides whether Refresh Shortlist auto-adds a candidate - there is no
        separate manual review step. Turn the checkbox on to bypass both and see every candidate Goonmetrics
        could price, regardless of margin or profit.
      </Text>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Purchase Source</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <RegionSelect label="Purchase region (default: The Forge)" value={form.purchase_region_id}
          onChange={(v) => set('purchase_region_id', v)} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Ore &amp; Mineral Inputs (Shopping List)</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <HubSelect label="Input market hub" description="Where ore/ice and minerals are priced (independent of Trading's)"
          allowAll value={form.input_hub_region_id} onChange={(v) => set('input_hub_region_id', v)} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Shortlist Size</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <Checkbox label="Cap the shortlist at a maximum size" checked={form.enforce_shortlist_cap}
          onChange={(e) => set('enforce_shortlist_cap', e.currentTarget.checked)} mt={6} />
        <NumberInput label="Max. active shortlist entries (when cap is on)" value={form.max_active_shortlist_items}
          min={1} step={10} onChange={(v) => set('max_active_shortlist_items', Number(v))} />
      </SimpleGrid>
      <Text size="xs" c="dimmed">
        Off by default - the margin/profit threshold above is the real filter. Turn this on only if too many
        candidates still clear that bar.
      </Text>

      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Settings
      </Button>
    </Stack>
  )
}
