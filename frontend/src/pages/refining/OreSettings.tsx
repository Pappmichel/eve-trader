import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, SimpleGrid, NumberInput, TextInput, Select, Button, Group, Center, Loader, ActionIcon } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { refiningApi } from '../../api/client'
import type { RefiningSettings as RefiningSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { HubSelect } from '../../components/HubSelect'

export default function OreSettings() {
  const { data } = useQuery({ queryKey: ['refining', 'settings'], queryFn: refiningApi.settings })
  const { data: options } = useQuery({ queryKey: ['refining', 'settings-options'], queryFn: refiningApi.settingsOptions })

  const [form, setForm] = useState<RefiningSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  // Confirmed real bug (code review, 2026-10-01): typing "-0.5" into this
  // field landed as 0.5 - sign silently lost. Root cause, verified with a
  // real NumberInput render: Mantine's own onChange reports the *raw string*
  // for an in-progress value like "-0" or "0." (deliberately, so "0.00"
  // isn't normalized away mid-type) - the previous `Number(v)` on every
  // keystroke turned that "-0" into the JS value -0, which lost its sign the
  // moment it round-tripped back in as the controlled `value` prop (`String(
  // -0) === "0"`, a JS quirk, not a Mantine bug). Every "-0.x" security value
  // is typed through exactly that "-0" intermediate state, so this hit any
  // negative value between -1 and 0, not just -0.5. Fix: keep the field's own
  // live value as whatever onChange reports (string mid-type, number once
  // complete) in a separate draft, and only write a real number into `form`
  // once Mantine itself reports one - never re-derive a number from a
  // partial string ourselves.
  const [securityDraft, setSecurityDraft] = useState<number | string>(0)
  useEffect(() => { if (data) setSecurityDraft(data.security_status) }, [data])

  const [newFamily, setNewFamily] = useState('')
  const [newLevel, setNewLevel] = useState<number | ''>(0)

  const save = useAction('Save Settings', refiningApi.updateSettings, [['refining', 'settings']])

  if (!form || !options) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof RefiningSettingsT>(key: K, value: RefiningSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  const families = Object.entries(form.ore_family_skill_levels).sort(([a], [b]) => a.localeCompare(b))
  const familyColumns: ColumnDef<{ family: string; level: number }, any>[] = [
    { header: 'Family', accessorKey: 'family', size: 220 },
    { header: 'Level', accessorKey: 'level', size: 100 },
    {
      header: '', id: 'remove', size: 60, enableSorting: false, enableResizing: false,
      cell: (i) => (
        <ActionIcon size="sm" variant="subtle" color="danger" aria-label={`Remove ${i.row.original.family}`} onClick={() => {
          const next = { ...form.ore_family_skill_levels }
          delete next[i.row.original.family]
          set('ore_family_skill_levels', next)
        }}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
  ]

  return (
    <Stack maw={800}>
      <HintCard>Changes take effect immediately. Structure/rig/security/implant/skills are entered manually here, not pulled from ESI.</HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Ore/Ice Reprocessing Setup</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <Select label="Structure" data={options.structure_types} value={form.structure_type}
          onChange={(v) => v && set('structure_type', v)} />
        <Select label="Rig" data={options.rig_tiers} value={form.rig_tier}
          onChange={(v) => v && set('rig_tier', v)} />
        <NumberInput label="System security (-1 .. 1)" value={securityDraft} min={-1} max={1} step={0.1}
          onChange={(v) => {
            setSecurityDraft(v)
            if (typeof v === 'number') set('security_status', v)
          }} />
        <Select label="Implant" data={options.implants} value={form.implant}
          onChange={(v) => v && set('implant', v)} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Skills</Title>
      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }}>
        <NumberInput label="Reprocessing" value={form.reprocessing_skill_level} min={0} max={5} step={1}
          onChange={(v) => set('reprocessing_skill_level', Number(v))} />
        <NumberInput label="Reprocessing Efficiency" value={form.reprocessing_efficiency_skill_level} min={0} max={5} step={1}
          onChange={(v) => set('reprocessing_efficiency_skill_level', Number(v))} />
        <NumberInput label="Scrapmetal Processing" value={form.scrapmetal_processing_skill_level} min={0} max={5} step={1}
          onChange={(v) => set('scrapmetal_processing_skill_level', Number(v))} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Ore/Ice Family Skills</Title>
      <Text size="xs" c="dimmed">
        One skill per ore/ice family (e.g. "Veldspar Processing" covers Veldspar/Concentrated Veldspar/Dense
        Veldspar) - a family missing here is assumed maxed (level 5). Add one only to record a lower level.
      </Text>
      {families.length > 0 && (
        <DataTable
          data={families.map(([family, level]) => ({ family, level }))}
          columns={familyColumns}
          getRowId={(r) => r.family}
          maxHeight={320}
          exportFilename="ore-family-skills"
        />
      )}
      <Group align="flex-end">
        <TextInput label="Family (e.g. Veldspar)" value={newFamily} onChange={(e) => setNewFamily(e.currentTarget.value)} />
        <NumberInput label="Level" value={newLevel} min={0} max={5} step={1} w={100}
          onChange={(v) => setNewLevel(v === '' ? '' : Number(v))} />
        <Button size="xs" variant="default" disabled={!newFamily.trim() || newLevel === ''} onClick={() => {
          set('ore_family_skill_levels', { ...form.ore_family_skill_levels, [newFamily.trim()]: Number(newLevel) })
          setNewFamily('')
          setNewLevel(0)
        }}>
          Add
        </Button>
      </Group>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Economy</Title>
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <HubSelect label="Market hub" description="Where ore/ice and minerals are priced (independent of Trading's)"
          allowAll value={form.hub_region_id} onChange={(v) => set('hub_region_id', v)} />
        <NumberInput label="Refining tax" suffix="%" decimalScale={2} value={form.refining_tax_rate * 100}
          min={0} max={100} step={1} onChange={(v) => set('refining_tax_rate', Number(v) / 100)} />
      </SimpleGrid>

      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Settings
      </Button>
    </Stack>
  )
}
