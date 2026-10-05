import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionIcon, Button, CopyButton, Group, NumberInput, Select, SimpleGrid, Stack, Switch, Table, Text, Textarea,
  TextInput,
} from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'

import { piApi } from '../../api/client'
import type { PiLayoutPayload, PiPlanet } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { notify } from '../../notify'
import { dateTime } from '../../format'
import { AnalysisView, Loading, PlanetPicker, QueryError, SectionTitle, num, usePiMeta } from './common'

export default function Templates() {
  const queryClient = useQueryClient()
  const { data: meta } = usePiMeta()
  const { data: list, isLoading, error } = useQuery({ queryKey: ['pi', 'templates'], queryFn: piApi.templates })

  // The pasted text is sent to the backend as a string, never re-serialised.
  const [text, setText] = useState('')
  const [payload, setPayload] = useState<PiLayoutPayload | null>(null)
  const [name, setName] = useState('')
  const [useRealPlanet, setUseRealPlanet] = useState(false)
  const [planet, setPlanet] = useState<PiPlanet | null>(null)
  const [radius, setRadius] = useState<number | string>('')
  const [retargetType, setRetargetType] = useState<string | null>(null)
  const [retargetProduct, setRetargetProduct] = useState<string | null>(null)

  const fail = (title: string) => (e: unknown) =>
    notify({ title, message: e instanceof Error ? e.message : String(e), color: 'danger' })

  const geometry = () => ({
    planet_id: useRealPlanet && planet ? planet.planet_id : undefined,
    radius_km: !useRealPlanet && radius !== '' ? Number(radius) : undefined,
  })

  const analyse = useMutation({
    mutationFn: () => piApi.validateLayout({ template: text, ...geometry() }),
    onSuccess: setPayload,
    onError: fail('Template analysis failed'),
  })

  const retarget = useMutation({
    mutationFn: () => piApi.retargetLayout({
      template: text,
      planet_type_id: retargetType ? Number(retargetType) : undefined,
      product_type_id: retargetProduct ? Number(retargetProduct) : undefined,
      ...geometry(),
    }),
    onSuccess: (res) => { setPayload(res); setText(res.template_json) },
    onError: fail('Retarget failed'),
  })

  const open = useMutation({
    mutationFn: (id: number) => piApi.template(id),
    onSuccess: (res) => { setPayload(res); setText(res.template_json); setName(res.name) },
    onError: fail('Could not open template'),
  })

  const save = useAction('Save template', () => piApi.saveTemplate({
    template: text, name: name.trim() || undefined, source: 'paste',
  }), [['pi', 'templates']])
  const remove = useAction('Delete template', (id: number) => piApi.deleteTemplate(id), [['pi', 'templates']])

  const copyExport = async (id: number) => {
    try {
      const res = await queryClient.fetchQuery({ queryKey: ['pi', 'export', id], queryFn: () => piApi.exportTemplate(id), staleTime: 0 })
      await navigator.clipboard.writeText(res.json)
      notify({ title: 'Copied', message: `${res.name} copied to the clipboard`, color: 'accent' })
    } catch (e) {
      fail('Export failed')(e)
    }
  }

  return (
    <Stack>
      <SectionTitle>Analyse a template</SectionTitle>
      <Textarea label="Template JSON" description="Paste an EVE PI template (the JSON from the in-game export or from this tool)"
        autosize minRows={5} maxRows={14} value={text} onChange={(e) => { setText(e.currentTarget.value); setPayload(null) }}
        styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }} />

      <Switch label="Check against a real planet" checked={useRealPlanet} onChange={(e) => setUseRealPlanet(e.currentTarget.checked)} />
      {useRealPlanet ? <PlanetPicker onChange={(p) => setPlanet(p)} /> : (
        <NumberInput label="Planet radius (km, optional)" w={220} min={50} max={200000} value={radius} onChange={setRadius}
          description="Link lengths are checked against it" />
      )}

      <Group>
        <Button disabled={!text.trim()} loading={analyse.isPending} onClick={() => analyse.mutate()}>Analyse</Button>
        <CopyButton value={text}>
          {({ copied, copy }) => <Button variant="default" disabled={!text.trim()} onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>}
        </CopyButton>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <Select label="Retarget: planet type" clearable placeholder="keep" value={retargetType} onChange={setRetargetType}
          data={(meta?.planet_types ?? []).map((t) => ({ value: String(t.type_id), label: t.name }))} />
        <Select label="Retarget: product (same tier)" clearable searchable placeholder="keep" value={retargetProduct}
          onChange={setRetargetProduct}
          data={(meta?.products ?? []).map((p) => ({ value: String(p.type_id), label: `${p.name} (P${p.tier})` }))} />
        <Group align="flex-end">
          <Button variant="default" disabled={!text.trim() || (!retargetType && !retargetProduct)}
            loading={retarget.isPending} onClick={() => retarget.mutate()}>Retarget</Button>
        </Group>
      </SimpleGrid>

      {payload && (
        <Stack>
          <Group align="flex-end">
            <TextInput label="Name" w={280} value={name} onChange={(e) => setName(e.currentTarget.value)} placeholder="Template name" />
            <Button variant="default" loading={save.isPending} disabled={!text.trim()} onClick={() => save.mutate()}>Save template</Button>
          </Group>
          <AnalysisView analysis={payload.analysis} />
        </Stack>
      )}

      <SectionTitle>Saved templates</SectionTitle>
      {error ? <QueryError error={error} /> : null}
      {isLoading ? <Loading /> : (
        <Table withRowBorders={false} verticalSpacing={4}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th><Table.Th>Planet type</Table.Th><Table.Th>CC</Table.Th>
              <Table.Th ta="right">Diameter</Table.Th><Table.Th>Source</Table.Th><Table.Th>Saved</Table.Th><Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(list ?? []).map((t) => {
              const type = meta?.planet_types.find((p) => p.type_id === t.planet_type_id)?.name
              return (
                <Table.Tr key={t.template_id}>
                  <Table.Td>{t.name}</Table.Td>
                  <Table.Td>{type ?? '–'}</Table.Td>
                  <Table.Td>{t.cc_level ?? '–'}</Table.Td>
                  <Table.Td ta="right">{t.diameter_km ? `${num(t.diameter_km, 0)} km` : '–'}</Table.Td>
                  <Table.Td>{t.source}</Table.Td>
                  <Table.Td>{dateTime(t.created_at)}</Table.Td>
                  <Table.Td>
                    <Group gap="xs" justify="flex-end" wrap="nowrap">
                      <Button size="compact-xs" variant="default" loading={open.isPending && open.variables === t.template_id}
                        onClick={() => open.mutate(t.template_id)}>Open</Button>
                      <Button size="compact-xs" variant="default" onClick={() => copyExport(t.template_id)}>Export</Button>
                      <ActionIcon size="sm" variant="subtle" color="danger" aria-label={`Delete ${t.name}`}
                        onClick={() => { if (window.confirm(`Delete template "${t.name}"?`)) remove.mutate(t.template_id) }}>
                        <IconTrash size={14} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              )
            })}
            {(list ?? []).length === 0 && <Table.Tr><Table.Td colSpan={7}><Text c="dimmed">No saved templates yet.</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  )
}
