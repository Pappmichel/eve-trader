import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Stack, Title, Text, SimpleGrid, NumberInput, Select, Switch, MultiSelect, Button, Center, Loader } from '@mantine/core'

import { doctrineApi } from '../../api/client'
import type { DoctrineSettings as DoctrineSettingsT, ReserveHangarOption } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { useStructureNameOptions } from '../../hooks/useStaticOptions'
import { HintCard } from '../../components/HintCard'
import { HubSelect } from '../../components/HubSelect'
import { StructureIdListField } from '../../components/StructureIdListField'

function locationLabel(o: { location_id: number; name: string | null }) {
  return o.name ? `${o.name} (${o.location_id})` : String(o.location_id)
}

function hangarKey(ownerType: string | null, ownerId: number | null, locationId: number | null, flag: string | null) {
  return ownerType && ownerId && locationId && flag ? `${ownerType}|${ownerId}|${locationId}|${flag}` : null
}

function hangarLabel(o: ReserveHangarOption) {
  const division = o.division_name ? `${o.hangar_flag} (${o.division_name})` : o.hangar_flag
  return `${o.owner_name ?? o.owner_id} - ${o.location_name ?? o.location_id} - ${division}`
}

export default function DoctrineSettings() {
  const { data } = useQuery({ queryKey: ['doctrine', 'settings'], queryFn: doctrineApi.settings })
  const { data: reserveHangars } = useQuery({
    queryKey: ['doctrine', 'strategic-reserve', 'hangars'], queryFn: doctrineApi.reserveHangars,
  })
  const { data: hangarOptions } = useQuery({
    queryKey: ['doctrine', 'hangar-division-options'], queryFn: doctrineApi.hangarDivisionOptions,
  })
  const { data: buyerOptions } = useQuery({
    queryKey: ['doctrine', 'buyer-character-options'], queryFn: doctrineApi.buyerCharacterOptions,
  })
  const { data: structureNames } = useStructureNameOptions()
  const [form, setForm] = useState<DoctrineSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const save = useAction('Save Settings', doctrineApi.updateSettings, [
    ['doctrine', 'settings'], ['doctrine', 'equivalents'], ['doctrine', 'strategic-reserve'], ['doctrine', 'stockpile'],
    ['doctrine', 'shopping-list'], ['doctrine', 'assemblable'],
  ])

  if (!form) return <Center h={200}><Loader color="accent" /></Center>

  const missingShares = (buyerOptions ?? [])
    .filter((o) => form.buyer_character_ids.includes(o.character_id))
    .flatMap((o) => [
      ...(o.assets_shared ? [] : [`${o.character_name}: Assets`]),
      ...(o.market_orders_shared ? [] : [`${o.character_name}: Market Orders`]),
    ])

  // Saved state, not the unsaved form: the sync reads what is stored.
  const noStructure = (data?.effective_structure_ids ?? []).length === 0
  const fallbackStructures = (data?.contract_structures ?? []).map(locationLabel).join(', ')
  const fallbackStockpile = form.doctrine_structure_ids.length > 0
    ? 'the contract structures above'
    : (data?.stockpile_locations ?? []).map(locationLabel).join(', ')

  const set = <K extends keyof DoctrineSettingsT>(key: K, value: DoctrineSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  const reserveValue = hangarKey(form.strategic_reserve_owner_type, form.strategic_reserve_owner_id,
    form.strategic_reserve_location_id, form.strategic_reserve_hangar_flag)
  const reserveOptions = (reserveHangars ?? []).map((o) => ({
    value: hangarKey(o.owner_type, o.owner_id, o.location_id, o.hangar_flag) as string, label: hangarLabel(o),
  }))
  // A saved hangar that holds nothing right now stays selectable.
  if (reserveValue && !reserveOptions.some((o) => o.value === reserveValue)) {
    reserveOptions.unshift({
      value: reserveValue,
      label: `${form.strategic_reserve_hangar_flag} at ${form.strategic_reserve_location_id} (empty or not shared)`,
    })
  }
  const setReserveHangar = (value: string | null) => {
    const [ownerType, ownerId, locationId, flag] = value ? value.split('|') : [null, null, null, null]
    setForm((f) => (f ? {
      ...f,
      strategic_reserve_owner_type: ownerType as DoctrineSettingsT['strategic_reserve_owner_type'],
      strategic_reserve_owner_id: ownerId ? Number(ownerId) : null,
      strategic_reserve_location_id: locationId ? Number(locationId) : null,
      strategic_reserve_hangar_flag: flag,
    } : f))
  }

  return (
    <Stack maw={700}>
      <HintCard>Changes take effect immediately. The scheduled sync interval is configured on the Trading Settings page.</HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Structure &amp; Stockpile</Title>
      {noStructure && (
        <Alert color="warn" variant="light" title="No contract structure set">
          Neither Doctrine nor Trading has a structure, so the contract sync cannot run. Add at least one
          contract structure below and save.
        </Alert>
      )}
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <StructureIdListField label="Contract structures" value={form.doctrine_structure_ids}
          description={form.doctrine_structure_ids.length === 0
            ? (fallbackStructures ? `None set - using Trading's structure: ${fallbackStructures}.`
              : 'None set, and Trading has no structure either.')
            : "Contracts at any of these count toward each fitting's contract target."}
          onChange={(v) => set('doctrine_structure_ids', v)} structureNames={structureNames} />
        <StructureIdListField label="Stockpile locations" value={form.stockpile_location_ids}
          description={form.stockpile_location_ids.length === 0
            ? `None set - using ${fallbackStockpile || 'the contract structures'}.`
            : "Stock at any of these counts toward each fitting's stockpile target."}
          onChange={(v) => set('stockpile_location_ids', v)} structureNames={structureNames} />
      </SimpleGrid>
      <Text size="xs" c="dimmed">
        Jita imports for every tool land in one shared corp intake division first (EVE has no API to move
        items between hangar divisions - see the Sorting tool). If you've sorted Doctrine's own
        contract materials into specific division(s), select them here so stockpile Ist only counts material
        actually set aside for Doctrine. Leave empty to count every division (today's default behaviour).
      </Text>
      <MultiSelect label="Hangar divisions counted as Doctrine stockpile" data={hangarOptions?.hangar_division_flags ?? []}
        value={form.stockpile_hangar_flags} onChange={(v) => set('stockpile_hangar_flags', v)}
        placeholder="All divisions" clearable />

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Strategic Reserve</Title>
      <Select label="Reserve hangar" searchable clearable placeholder="None" data={reserveOptions}
        description="Production and Trading treat everything in this hangar as if it did not exist, and it does not count as Doctrine stockpile. Total Wealth and Realized still see it."
        value={reserveValue} onChange={setReserveHangar} />
      <Switch label="Reserve fits include the hull" checked={form.strategic_reserve_include_hulls}
        onChange={(e) => set('strategic_reserve_include_hulls', e.currentTarget.checked)} />

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Validation</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <NumberInput label="Cargo/drone/charge tolerance" suffix="%" decimalScale={2}
          value={form.cargo_tolerance_pct * 100} min={0} max={100} step={5}
          onChange={(v) => set('cargo_tolerance_pct', Number(v) / 100)} />
      </SimpleGrid>
      <Switch label="Treat unexpected extra items as a defect (strict mode)" checked={form.strict_extras}
        onChange={(e) => set('strict_extras', e.currentTarget.checked)} />
      <Switch label="Accept equivalent variants" checked={form.accept_equivalent_variants}
        onChange={(e) => set('accept_equivalent_variants', e.currentTarget.checked)} />
      <Text size="xs" c="dimmed">
        Modules/rigs/subsystems need an exact match, or an equivalent variant from the Equivalents tab
        (identical stats, e.g. Dark Blood for True Sansha) while the switch above is on - those count for
        contracts and stockpile, and the Shopping List buys the cheapest one. Drones, cargo, and loaded charges
        only need to clear the tolerance above to count as "tolerable" instead of "critical".
      </Text>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Shopping List</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <HubSelect label="Market hub" description="Order book the Shopping List buys from (its own setting, independent of Trading's)"
          allowAll value={form.hub_region_id} onChange={(v) => set('hub_region_id', v)} />
        <NumberInput label="Jita import cost (ISK/m³)" value={form.import_cost_per_m3} min={0} step={50}
          onChange={(v) => set('import_cost_per_m3', Number(v))} />
      </SimpleGrid>
      <Text size="xs" c="dimmed">
        Only affects the Shopping List's own Buy-Jita price (a separate setting from Production's own freight
        cost, seeded from the same 900 ISK/m³ default) - fitted modules/ships plausibly have different logistics
        than raw production materials.
      </Text>
      <MultiSelect label="Buyer characters" searchable clearable placeholder="None"
        description="Their hangars and open buy orders anywhere in the market hub's region reduce what the Shopping List still asks to buy"
        data={(buyerOptions ?? []).map((o) => ({ value: String(o.character_id), label: o.character_name }))}
        value={form.buyer_character_ids.map(String)}
        onChange={(v) => set('buyer_character_ids', v.map(Number))} />
      {missingShares.length > 0 && (
        <Text size="xs" c="warn">
          Not shared with Doctrine yet (Characters page): {missingShares.join(', ')}.
        </Text>
      )}

      <Button mt="md" w={200} loading={save.isPending} onClick={() => save.mutate({
        // Only the editable fields - the effective/name lists are read-only.
        doctrine_structure_ids: form.doctrine_structure_ids,
        stockpile_location_ids: form.stockpile_location_ids,
        cargo_tolerance_pct: form.cargo_tolerance_pct,
        strict_extras: form.strict_extras,
        accept_equivalent_variants: form.accept_equivalent_variants,
        import_cost_per_m3: form.import_cost_per_m3,
        hub_region_id: form.hub_region_id,
        stockpile_hangar_flags: form.stockpile_hangar_flags,
        buyer_character_ids: form.buyer_character_ids,
        strategic_reserve_owner_type: form.strategic_reserve_owner_type,
        strategic_reserve_owner_id: form.strategic_reserve_owner_id,
        strategic_reserve_location_id: form.strategic_reserve_location_id,
        strategic_reserve_hangar_flag: form.strategic_reserve_hangar_flag,
        strategic_reserve_include_hulls: form.strategic_reserve_include_hulls,
      })}>
        Save Settings
      </Button>
    </Stack>
  )
}
