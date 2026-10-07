import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import {
  ActionIcon, Alert, Badge, Button, CopyButton, Group, Modal, NumberInput, Paper, SimpleGrid, Stack,
  Switch, Table, Text, Textarea, TextInput,
} from '@mantine/core'

import { piApi } from '../../api/client'
import type { PiChainAssignment, PiChainPlanResult, PiGeneratePayload, PiSystemHit } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { notify } from '../../notify'
import { isk } from '../../format'
import { Loading, QueryError, SectionTitle, Stat, SystemSearch, iskPerDay, num } from './common'
import type { EditorOpenState } from './Editor'

interface CharRow { key: number; name: string; planets: number | string; cc: number | string }

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  optimal: { label: 'Optimal', color: 'accent' },
  time_limit: { label: 'Best plan found within the time limit', color: 'warn' },
  fallback: { label: 'Fallback plan', color: 'warn' },
  infeasible: { label: 'Infeasible', color: 'danger' },
}

export function ChainPlanResultView({ result }: { result: PiChainPlanResult }) {
  const navigate = useNavigate()
  const [modal, setModal] = useState<{ a: PiChainAssignment; g: PiGeneratePayload } | null>(null)
  const status = STATUS_LABEL[result.status] ?? { label: result.status, color: 'gray' }

  const generate = useMutation({
    mutationFn: (a: PiChainAssignment) =>
      piApi.generateLayout(a.layout_request as unknown as Parameters<typeof piApi.generateLayout>[0])
        .then((g) => ({ a, g })),
    onSuccess: setModal,
    onError: (e: unknown) =>
      notify({ title: 'Template generation failed', message: e instanceof Error ? e.message : String(e), color: 'danger' }),
  })
  const saveTemplate = useAction('Save template', (p: { template: unknown; name: string }) =>
    piApi.saveTemplate({ template: p.template, name: p.name, source: 'generated' }), [['pi', 'templates']])

  const byCharacter = useMemo(() => {
    const m = new Map<string, { name: string; cc: number; rows: PiChainAssignment[] }>()
    for (const a of result.assignments) {
      const g = m.get(a.character_key) ?? { name: a.character, cc: a.cc_level, rows: [] }
      g.rows.push(a)
      m.set(a.character_key, g)
    }
    return [...m.entries()]
  }, [result.assignments])

  const colonyName = (a: PiChainAssignment) => `${a.product_name} - ${a.planet_name} (${a.character})`

  const openInEditor = (a: PiChainAssignment, g: PiGeneratePayload) => {
    const state: EditorOpenState = {
      template: g.template, template_json: g.template_json,
      planet_id: g.planet.planet_id ?? undefined, radius_km: g.planet.radius_km,
      name: colonyName(a), source: 'generated',
    }
    navigate('/pi/editor', { state })
  }

  return (
    <Stack>
      <Group gap="sm">
        <Text fw={700} size="lg">{result.target_name}</Text>
        <Badge color={status.color} variant="light">{status.label}</Badge>
        {result.system?.name && <Badge variant="outline">{result.system.name}</Badge>}
        <Badge variant="outline">{result.zone}</Badge>
      </Group>
      <SimpleGrid cols={{ base: 2, sm: 5 }}>
        <Stat label="Output / hour" value={num(result.target_units_per_day / 24, 2)}
          hint={[
            result.requested_units_per_day != null ? `Wanted ${num(result.requested_units_per_day / 24, 2)} /h` : null,
            result.max_target_units_per_day != null ? `Maximum ${num(result.max_target_units_per_day / 24, 2)} /h` : null,
          ].filter((s): s is string => s !== null).join(' · ') || undefined} />
        <Stat label="Output / day" value={num(result.target_units_per_day, 1)} />
        <Stat label="Profit / day" value={iskPerDay(result.profit_per_day)} />
        <Stat label="Profit / slot / day" value={result.profit_per_slot === null ? '–' : iskPerDay(result.profit_per_slot)} />
        <Stat label="Slots used" value={`${result.used_slots} / ${result.slots}`} hint={`${result.free_slots} free`} />
      </SimpleGrid>
      {result.requested_units_per_day != null && (
        <Text size="sm">
          Wanted {num(result.requested_units_per_day / 24, 2)} /h, making {num(result.target_units_per_day / 24, 2)} /h
          {result.max_target_units_per_day != null ? ` (maximum ${num(result.max_target_units_per_day / 24, 2)} /h)` : ''}.
        </Text>
      )}
      {result.notes.map((n, i) => <Alert key={i} color="info" py={6}>{n}</Alert>)}

      <SectionTitle>Colonies per character</SectionTitle>
      {byCharacter.length === 0 && <Text size="sm" c="dimmed">No colonies in this plan.</Text>}
      {byCharacter.map(([key, c]) => (
        <Paper key={key} withBorder p="sm">
          <Group gap="sm" mb={4}>
            <Text fw={600}>{c.name}</Text>
            <Badge variant="outline">CC {c.cc}</Badge>
            <Text size="xs" c="dimmed">{c.rows.length} colon{c.rows.length === 1 ? 'y' : 'ies'}</Text>
          </Group>
          <Table withRowBorders={false} verticalSpacing={3}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Planet</Table.Th><Table.Th>Chain</Table.Th><Table.Th>Product</Table.Th>
                <Table.Th ta="right">Units / day</Table.Th><Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {c.rows.map((a, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{a.planet_name}{a.planet_type ? ` (${a.planet_type})` : ''}</Table.Td>
                  <Table.Td>{a.chain}</Table.Td>
                  <Table.Td>{a.product_name}</Table.Td>
                  <Table.Td ta="right">{num(a.units_per_day, 1)}</Table.Td>
                  <Table.Td ta="right">
                    <Button size="compact-xs" variant="default" loading={generate.isPending && generate.variables === a}
                      onClick={() => generate.mutate(a)}>Template</Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      ))}

      <SectionTitle>Stages</SectionTitle>
      <Table withRowBorders={false} verticalSpacing={3}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Product</Table.Th><Table.Th>Tier</Table.Th>
            <Table.Th ta="right">Made / day</Table.Th><Table.Th ta="right">Used internally / day</Table.Th>
            <Table.Th ta="right">Bought / day</Table.Th><Table.Th ta="right">Sold / day</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {result.stages.map((s) => (
            <Table.Tr key={s.type_id}>
              <Table.Td>{s.name}</Table.Td><Table.Td>P{s.tier}</Table.Td>
              <Table.Td ta="right">{num(s.made, 1)}</Table.Td>
              <Table.Td ta="right">{num(s.internal, 1)}</Table.Td>
              <Table.Td ta="right">{num(s.bought, 1)}</Table.Td>
              <Table.Td ta="right">{num(s.sold, 1)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <SectionTitle>Purchases</SectionTitle>
      {result.purchases.length === 0 ? <Text size="sm" c="dimmed">Nothing needs to be bought.</Text> : (
        <Table withRowBorders={false} verticalSpacing={3}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Product</Table.Th><Table.Th ta="right">Units / day</Table.Th>
              <Table.Th ta="right">Cost / day</Table.Th><Table.Th>Why</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {result.purchases.map((p, i) => (
              <Table.Tr key={i}>
                <Table.Td>{p.name}</Table.Td>
                <Table.Td ta="right">{num(p.units_per_day, 1)}</Table.Td>
                <Table.Td ta="right">{isk(p.cost_per_day)}</Table.Td>
                <Table.Td>{p.reason}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Modal opened={modal !== null} onClose={() => setModal(null)} size="lg"
        title={modal ? `Template: ${colonyName(modal.a)}` : ''}>
        {modal && (
          <Stack>
            <Group>
              <CopyButton value={modal.g.template_json}>
                {({ copied, copy }) => <Button size="xs" variant="default" onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>}
              </CopyButton>
              <Button size="xs" onClick={() => openInEditor(modal.a, modal.g)}>Open in editor</Button>
              <Button size="xs" variant="default" loading={saveTemplate.isPending}
                onClick={() => saveTemplate.mutate({ template: modal.g.template, name: colonyName(modal.a) })}>
                Save to library
              </Button>
            </Group>
            <Textarea readOnly autosize minRows={4} maxRows={14} value={modal.g.template_json}
              aria-label="Template JSON" styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }} />
            {modal.g.notes.map((n, i) => <Text key={i} size="sm" c="dimmed">{n}</Text>)}
          </Stack>
        )}
      </Modal>
    </Stack>
  )
}

let rowKey = 0

export default function ChainPlanner({ productId, targetPerHour }: { productId: number | null; targetPerHour: number | null }) {
  const [system, setSystem] = useState<PiSystemHit | null>(null)
  const [manual, setManual] = useState(false)
  const [rows, setRows] = useState<CharRow[]>([{ key: rowKey++, name: 'Main', planets: 6, cc: 5 }])
  const [ownerTax, setOwnerTax] = useState<number | string>('')
  const [allowBuy, setAllowBuy] = useState(false)

  const plan = useMutation({
    mutationFn: () => {
      if (productId === null || targetPerHour === null) throw new Error('Choose a product and a wanted output per hour')
      return piApi.chainPlan({
        product_type_id: productId,
        solar_system_id: system?.solar_system_id,
        characters: manual
          ? rows.map((r) => ({ name: r.name.trim() || undefined, planets: Number(r.planets) || 1, cc_level: Number(r.cc) }))
          : undefined,
        owner_tax_rate: ownerTax === '' ? undefined : Number(ownerTax) / 100,
        allow_buy: allowBuy,
        target_per_hour: targetPerHour,
      })
    },
  })

  const update = (key: number, patch: Partial<CharRow>) =>
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)))

  return (
    <Stack>
      <SectionTitle>Plan this chain in a system</SectionTitle>
      <Text size="xs" c="dimmed">
        Finds the most profitable way to make the wanted output per hour: which colonies, on which planets,
        for which characters. Pick a solar system to use its planets, or leave it empty for one generic planet
        of each type. Owner tax, the character list and buying intermediates still apply.
      </Text>
      <Paper withBorder p="md">
        <Stack gap="sm">
          <SystemSearch label="Solar system (optional)" onPick={setSystem} />
          <Switch label="Edit the character list manually" checked={manual}
            onChange={(e) => setManual(e.currentTarget.checked)} />
          {!manual ? (
            <Text size="xs" c="dimmed">Using your characters (ESI skills, or the PI settings when none are available).</Text>
          ) : (
            <Stack gap={4}>
              {rows.map((r) => (
                <Group key={r.key} align="flex-end" gap="xs">
                  <TextInput label="Name" w={180} value={r.name} onChange={(e) => update(r.key, { name: e.currentTarget.value })} />
                  <NumberInput label="Planets" w={90} min={1} max={6} value={r.planets} onChange={(v) => update(r.key, { planets: v })} />
                  <NumberInput label="CC level" w={90} min={0} max={5} value={r.cc} onChange={(v) => update(r.key, { cc: v })} />
                  <ActionIcon variant="subtle" color="danger" aria-label="Remove character" disabled={rows.length <= 1}
                    onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))}>x</ActionIcon>
                </Group>
              ))}
              <Group>
                <Button size="xs" variant="default" disabled={rows.length >= 30}
                  onClick={() => setRows((rs) => [...rs, { key: rowKey++, name: `Character ${rs.length + 1}`, planets: 6, cc: 5 }])}>
                  Add character
                </Button>
              </Group>
            </Stack>
          )}
          <Group align="flex-end">
            <NumberInput label="Owner customs tax" suffix="%" min={0} max={100} w={180} placeholder="Settings default"
              value={ownerTax} onChange={setOwnerTax} />
          </Group>
          <Switch label="Also buy intermediates when that pays more" checked={allowBuy}
            onChange={(e) => setAllowBuy(e.currentTarget.checked)}
            description="Off: a real chain from P0, buying only what the system cannot make." />
          <Group>
            <Button onClick={() => plan.mutate()} loading={plan.isPending}
              disabled={productId === null || targetPerHour === null}>Plan chain</Button>
            {(productId === null || targetPerHour === null) && (
              <Text size="xs" c="dimmed">Choose a product and a wanted output per hour first.</Text>
            )}
          </Group>
        </Stack>
      </Paper>
      {plan.isPending && (
        <>
          <Loading />
          <Text ta="center" size="sm" c="dimmed">Planning the chain - this can take up to 40 seconds.</Text>
        </>
      )}
      {plan.error ? <QueryError error={plan.error} /> : null}
      {plan.data && !plan.isPending && <ChainPlanResultView result={plan.data} />}
    </Stack>
  )
}
