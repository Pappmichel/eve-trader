import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, SimpleGrid, NumberInput, TextInput, Select, Button, Card, Group, Center, Loader } from '@mantine/core'

import { productionApi } from '../../api/client'
import type { ProductionSettings as ProductionSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { useSolarSystemOptions, useStructureNameOptions } from '../../hooks/useStaticOptions'
import { HintCard } from '../../components/HintCard'
import { SearchableSelect } from '../../components/SearchableSelect'
import { StructureIdField } from '../../components/StructureIdField'

// 2 decimal places, not format.ts's own pct() (1 decimal) - real EVE system
// cost indices are small enough (e.g. 0.0231) that 1 decimal rounds away
// exactly the precision this hint exists to show (confirmed real 2026-08-27:
// a user misread "0.014" as "0.14", 5x higher than their real system's own
// index, making the Buy/Build lists worse instead of better).
function pctHint(value: number | undefined): string {
  return value === undefined ? '–' : `${(value * 100).toFixed(2)}%`
}

export default function ProductionSettings() {
  const { data } = useQuery({ queryKey: ['production', 'settings'], queryFn: productionApi.settings })
  const { data: structureOptions } = useQuery({ queryKey: ['production', 'structure-options'], queryFn: productionApi.structureOptions })
  const { data: systemSettings } = useQuery({ queryKey: ['production', 'system-settings'], queryFn: productionApi.systemSettings })
  const { data: costIndexHints } = useQuery({ queryKey: ['production', 'system-cost-indices'], queryFn: productionApi.systemCostIndices })
  const { data: solarSystemOptions } = useSolarSystemOptions()
  const { data: structureNames } = useStructureNameOptions()
  const systemSelectData = useMemo(
    () => (solarSystemOptions ?? []).map((s) => ({ value: String(s.solar_system_id), label: s.solar_system_name })),
    [solarSystemOptions],
  )

  const [form, setForm] = useState<ProductionSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const [componentSystemId, setComponentSystemId] = useState<string | null>(null)
  const [manufacturingSystemId, setManufacturingSystemId] = useState<string | null>(null)
  useEffect(() => {
    if (systemSettings) {
      setComponentSystemId(systemSettings.component_system_id != null ? String(systemSettings.component_system_id) : null)
      setManufacturingSystemId(systemSettings.manufacturing_system_id != null ? String(systemSettings.manufacturing_system_id) : null)
    }
  }, [systemSettings])

  const save = useAction('Save Settings', async (updates: ProductionSettingsT) => {
    const result = await productionApi.updateSettings(updates)
    // Best-effort name resolution for the home structure (GitHub issue #21 -
    // this was never resolved at all, so the Logistics tab's Distribution
    // section showed a raw numeric ID forever whenever no separate
    // distribution-source override was set, since home_location_id is the
    // fallback source in that case). Cached indefinitely once resolved
    // (do_resolve_structure_name), so re-running this on every settings
    // save is cheap - not a fresh ESI call after the first success.
    if (updates.home_location_id != null) {
      try {
        await productionApi.resolveStructureName(updates.home_location_id)
      } catch {
        // best-effort - the settings save above already succeeded
      }
    }
    return result
  }, [['production', 'settings'], ['production', 'structure-names']])
  const saveComponentSystem = useAction('Save Component System', () => {
    const opt = systemSelectData.find((o) => o.value === componentSystemId)
    return productionApi.setSystem('component', Number(componentSystemId), opt?.label ?? '')
  }, [['production', 'system-settings']])
  const saveManufacturingSystem = useAction('Save Manufacturing System', () => {
    const opt = systemSelectData.find((o) => o.value === manufacturingSystemId)
    return productionApi.setSystem('manufacturing', Number(manufacturingSystemId), opt?.label ?? '')
  }, [['production', 'system-settings']])

  // Same fix as TradingSettings.tsx - a bare `return null` rendered a
  // blank page during the initial fetch instead of a loading indicator.
  if (!form || !structureOptions) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof ProductionSettingsT>(key: K, value: ProductionSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  return (
    <Stack maw={800}>
      <HintCard>Changes take effect immediately and are saved to your account.</HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Economy</Title>
      <SimpleGrid cols={2}>
        <NumberInput label="Component overbuild buffer" suffix="%" decimalScale={2} value={form.component_overbuild * 100} min={0} step={5}
          onChange={(v) => set('component_overbuild', Number(v) / 100)} />
        <NumberInput label="Freight cost (ISK/m³)" value={form.haul_cost_per_m3} min={0} step={50}
          onChange={(v) => set('haul_cost_per_m3', Number(v))} />
        <NumberInput label="Market sell fees" suffix="%" decimalScale={2} value={form.market_fees * 100} min={0} max={100} step={0.5}
          onChange={(v) => set('market_fees', Number(v) / 100)} />
        <NumberInput label="Broker's fee buy" suffix="%" decimalScale={2} value={form.jita_buy_broker_fee * 100} min={0} max={100} step={0.1}
          onChange={(v) => set('jita_buy_broker_fee', Number(v) / 100)} />
        <NumberInput label="Minimum margin for build list" suffix="%" decimalScale={2} value={form.min_margin * 100} min={0} step={1}
          onChange={(v) => set('min_margin', Number(v) / 100)} />
        <NumberInput label="Minimum daily profit for Build Candidates (ISK)" value={form.min_daily_profit} min={0} step={1000}
          onChange={(v) => set('min_daily_profit', Number(v))} />
        <NumberInput label="BPC inventory" value={form.bpc_inventory} min={0} step={1}
          onChange={(v) => set('bpc_inventory', Number(v))} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Market &amp; Location</Title>
      <SimpleGrid cols={2}>
        <TextInput label="Home market (appraise.gnf.lt slug)" value={form.home_market ?? ''}
          onChange={(e) => set('home_market', e.currentTarget.value)} />
        <StructureIdField label="Structure/location ID (assets/orders)" value={form.home_location_id ?? null}
          onChange={(v) => set('home_location_id', v)} structureNames={structureNames} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Where You Build</Title>
      <Text size="xs" c="dimmed">
        Structure base bonus and rig bonus stack multiplicatively. Split by reactions / components (Tech II/capital
        components, tools, data interfaces, Tech III components) / manufacturing (everything else) - you can build
        in three different structures.
      </Text>
      <SimpleGrid cols={2}>
        <Select label="Structure - Reactions" data={structureOptions.structure_types} value={form.reaction_structure_type}
          onChange={(v) => v && set('reaction_structure_type', v)} />
        <Select label="Rig - Reactions" data={structureOptions.rig_tiers} value={form.reaction_rig_tier}
          onChange={(v) => v && set('reaction_rig_tier', v)} />
        <Select label="Structure - Components" data={structureOptions.structure_types} value={form.component_structure_type}
          onChange={(v) => v && set('component_structure_type', v)} />
        <Select label="Rig - Components" data={structureOptions.rig_tiers} value={form.component_rig_tier}
          onChange={(v) => v && set('component_rig_tier', v)} />
        <Select label="Structure - Manufacturing (everything else)" data={structureOptions.structure_types} value={form.manufacturing_structure_type}
          onChange={(v) => v && set('manufacturing_structure_type', v)} />
        <Select label="Rig - Manufacturing (everything else)" data={structureOptions.rig_tiers} value={form.manufacturing_rig_tier}
          onChange={(v) => v && set('manufacturing_rig_tier', v)} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Invention Skills</Title>
      <SimpleGrid cols={3}>
        <NumberInput label="Encryption Methods" value={form.encryption_skill_level} min={0} max={5} step={1}
          onChange={(v) => set('encryption_skill_level', Number(v))} />
        <NumberInput label="Datacore skill 1" value={form.datacore_skill_1_level} min={0} max={5} step={1}
          onChange={(v) => set('datacore_skill_1_level', Number(v))} />
        <NumberInput label="Datacore skill 2" value={form.datacore_skill_2_level} min={0} max={5} step={1}
          onChange={(v) => set('datacore_skill_2_level', Number(v))} />
      </SimpleGrid>

      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Settings
      </Button>

      <Card withBorder mt="lg">
        <Title order={6} c="dimmed" tt="uppercase" mb="xs">Solar Systems (Cost Index)</Title>
        <Text size="xs" c="dimmed" mb="sm">
          Pick from the local SDE system list. Reactions and components use the component system; everything else
          uses the manufacturing system. Still needed even if every Logistics category has its own structure - it's
          the fallback for any category not yet resolved to a system, and the only source for rig security-tier
          scaling. Each also has a manual override below (shown as "Live system index" underneath) that always wins
          when set, regardless of system - leave blank to keep using the picked system's live ESI cost index. Real
          EVE cost indices are usually a few percent, not tens of percent - check the live value shown before typing
          one in.
        </Text>
        <Stack gap="sm">
          <Group align="flex-end">
            <SearchableSelect label="Component/reaction system" placeholder="Search system…" data={systemSelectData}
              value={componentSystemId} onChange={setComponentSystemId} w={240} />
            <Button variant="default" disabled={!componentSystemId}
              onClick={() => saveComponentSystem.mutate()} loading={saveComponentSystem.isPending}>
              Save
            </Button>
            {systemSettings?.component_system_id && (
              <Text size="xs" c="dimmed">Current: {systemSettings.component_system_name} (ID {systemSettings.component_system_id})</Text>
            )}
          </Group>
          <SimpleGrid cols={2}>
            <Stack gap={2}>
              <NumberInput label="Reaction cost index override" suffix="%" decimalScale={2} placeholder="Auto (from system above)"
                value={form.reaction_cost_index_override != null ? form.reaction_cost_index_override * 100 : ''} min={0} max={100} step={0.1}
                onChange={(v) => set('reaction_cost_index_override', v === '' ? null : Number(v) / 100)} />
              <Text size="xs" c="dimmed">Live system index: {pctHint(costIndexHints?.component?.reaction)}</Text>
            </Stack>
            <Stack gap={2}>
              <NumberInput label="Component cost index override" suffix="%" decimalScale={2} placeholder="Auto (from system above)"
                value={form.component_cost_index_override != null ? form.component_cost_index_override * 100 : ''} min={0} max={100} step={0.1}
                onChange={(v) => set('component_cost_index_override', v === '' ? null : Number(v) / 100)} />
              <Text size="xs" c="dimmed">Live system index: {pctHint(costIndexHints?.component?.manufacturing)}</Text>
            </Stack>
          </SimpleGrid>
          <Group align="flex-end">
            <SearchableSelect label="Manufacturing system (everything else)" placeholder="Search system…" data={systemSelectData}
              value={manufacturingSystemId} onChange={setManufacturingSystemId} w={240} />
            <Button variant="default" disabled={!manufacturingSystemId}
              onClick={() => saveManufacturingSystem.mutate()} loading={saveManufacturingSystem.isPending}>
              Save
            </Button>
            {systemSettings?.manufacturing_system_id && (
              <Text size="xs" c="dimmed">Current: {systemSettings.manufacturing_system_name} (ID {systemSettings.manufacturing_system_id})</Text>
            )}
          </Group>
          <SimpleGrid cols={2}>
            <Stack gap={2}>
              <NumberInput label="Manufacturing cost index override" suffix="%" decimalScale={2} placeholder="Auto (from system above)"
                value={form.manufacturing_cost_index_override != null ? form.manufacturing_cost_index_override * 100 : ''} min={0} max={100} step={0.1}
                onChange={(v) => set('manufacturing_cost_index_override', v === '' ? null : Number(v) / 100)} />
              <Text size="xs" c="dimmed">Live system index: {pctHint(costIndexHints?.manufacturing?.manufacturing)}</Text>
            </Stack>
          </SimpleGrid>
          <Text size="xs" c="dimmed">
            Overrides are saved with the main <b>Save Settings</b> button above, not the per-system Save buttons here.
          </Text>
        </Stack>
      </Card>
    </Stack>
  )
}
