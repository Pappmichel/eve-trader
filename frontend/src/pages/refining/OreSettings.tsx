import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Text, SimpleGrid, NumberInput, TextInput, Select, Button, Group, Center, Loader, ActionIcon, Table } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'

import { refiningApi } from '../../api/client'
import type { RefiningSettings as RefiningSettingsT } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { HintCard } from '../../components/HintCard'

export default function OreSettings() {
  const { data } = useQuery({ queryKey: ['refining', 'settings'], queryFn: refiningApi.settings })
  const { data: options } = useQuery({ queryKey: ['refining', 'settings-options'], queryFn: refiningApi.settingsOptions })

  const [form, setForm] = useState<RefiningSettingsT | null>(null)
  useEffect(() => { if (data) setForm(data) }, [data])

  const [newFamily, setNewFamily] = useState('')
  const [newLevel, setNewLevel] = useState<number | ''>(0)

  const save = useAction('Save Settings', refiningApi.updateSettings, [['refining', 'settings']])

  if (!form || !options) return <Center h={200}><Loader color="accent" /></Center>

  const set = <K extends keyof RefiningSettingsT>(key: K, value: RefiningSettingsT[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f))

  const families = Object.entries(form.ore_family_skill_levels).sort(([a], [b]) => a.localeCompare(b))

  return (
    <Stack maw={800}>
      <HintCard>Changes take effect immediately. Structure/rig/security/implant/skills are entered manually here, not pulled from ESI.</HintCard>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Ore/Ice Reprocessing Setup</Title>
      <SimpleGrid cols={2}>
        <Select label="Structure" data={options.structure_types} value={form.structure_type}
          onChange={(v) => v && set('structure_type', v)} />
        <Select label="Rig" data={options.rig_tiers} value={form.rig_tier}
          onChange={(v) => v && set('rig_tier', v)} />
        <NumberInput label="System security (-1 .. 1)" value={form.security_status} min={-1} max={1} step={0.1}
          onChange={(v) => set('security_status', Number(v))} />
        <Select label="Implant" data={options.implants} value={form.implant}
          onChange={(v) => v && set('implant', v)} />
      </SimpleGrid>

      <Title order={6} c="dimmed" tt="uppercase" mt="md">Skills</Title>
      <SimpleGrid cols={3}>
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
        Veldspar) - a family missing here is treated as level 0.
      </Text>
      {families.length > 0 && (
        <Table>
          <Table.Tbody>
            {families.map(([family, level]) => (
              <Table.Tr key={family}>
                <Table.Td>{family}</Table.Td>
                <Table.Td>{level}</Table.Td>
                <Table.Td>
                  <ActionIcon size="sm" variant="subtle" color="danger" onClick={() => {
                    const next = { ...form.ore_family_skill_levels }
                    delete next[family]
                    set('ore_family_skill_levels', next)
                  }}>
                    <IconTrash size={14} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
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
      <SimpleGrid cols={2}>
        <NumberInput label="Refining tax (0-1)" value={form.refining_tax_rate} min={0} max={1} step={0.01}
          onChange={(v) => set('refining_tax_rate', Number(v))} />
      </SimpleGrid>

      <Button mt="md" w={240} onClick={() => save.mutate(form)} loading={save.isPending}>
        Save Settings
      </Button>
    </Stack>
  )
}
