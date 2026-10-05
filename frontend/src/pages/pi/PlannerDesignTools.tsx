import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  Alert, Badge, Button, CopyButton, Group, NumberInput, Paper, SimpleGrid, Stack, Table, Text,
} from '@mantine/core'

import { piApi } from '../../api/client'
import type {
  PiDesign, PiDesignBase, PiGeneratePayload, PiMixedP2, PiStorageSuggestion, PiWayRow, PiWays,
} from '../../api/types'
import { notify } from '../../notify'
import { isk } from '../../format'
import { SectionTitle, Stat, VerdictBadge, bufferText, iskPerDay, num, usePiMeta } from './common'

// The design keys the backend accepts (evaluation.design also carries named lists).
export function coreOf(d: PiDesign): PiDesign {
  return {
    chain: d.chain, product_type_id: d.product_type_id, planet_type_id: d.planet_type_id, cc_level: d.cc_level,
    factories: d.factories, ecus: d.ecus, launchpads: d.launchpads, storages: d.storages,
  }
}

interface Props {
  // Planet / zone / CC of the Planner form, null until it is complete.
  planetBits: PiDesignBase | null
  chain: string | null
  productId: number | null
  productTier: number | null
  intervalHours?: number
  yieldPerHead?: number
  // The design currently shown in the Planner (null before a first evaluation).
  currentDesign: PiDesign | null
  onUseDesign: (design: PiDesign) => void
  onOpenInEditor: (g: PiGeneratePayload) => void
}

const fail = (title: string) => (e: unknown) =>
  notify({ title, message: e instanceof Error ? e.message : String(e), color: 'danger' })

function WayTable({ rows, partial, onUse }: { rows: PiWayRow[]; partial: boolean; onUse: (r: PiWayRow) => void }) {
  return (
    <Table withRowBorders={false} verticalSpacing={4}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{partial ? 'Extracted P1' : 'Made here'}</Table.Th><Table.Th>Hauled in</Table.Th>
          <Table.Th ta="right">Output/h</Table.Th><Table.Th ta="right">Profit/day</Table.Th><Table.Th>Verdict</Table.Th><Table.Th />
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((r, i) => (
          <Table.Tr key={i}>
            <Table.Td>{partial ? r.extracted?.name ?? '–' : (r.made ?? []).map((m) => m.name).join(', ') || '–'}</Table.Td>
            <Table.Td>{r.hauled.map((h) => h.name).join(', ') || 'nothing'}</Table.Td>
            <Table.Td ta="right">{r.evaluation ? num(r.evaluation.effective_product_per_hour, 1) : '–'}</Table.Td>
            <Table.Td ta="right">{r.economics ? iskPerDay(r.economics.profit_per_day) : '–'}</Table.Td>
            <Table.Td>{r.economics ? <VerdictBadge worthIt={r.economics.worth_it} reason={r.economics.reason} /> : '–'}</Table.Td>
            <Table.Td>
              <Button size="compact-xs" variant="default" disabled={!r.evaluation} onClick={() => onUse(r)}>Use this design</Button>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  )
}

function StorageResult({ s, onApply }: { s: PiStorageSuggestion; onApply: (d: PiDesign) => void }) {
  const hours = (h: number | null | undefined) => bufferText(h)
  let text = ''
  switch (s.kind) {
    case 'covered': text = `Already covered: the buffer lasts ${hours(s.buffer_hours)} against the ${num(s.interval_hours, 0)} h interval.`; break
    case 'add_storage':
      text = `Add ${s.storages} storage facilit${s.storages === 1 ? 'y' : 'ies'}: buffer ${hours(s.buffer_hours)} `
        + `${s.reaches_interval ? 'reaches' : 'does not fully reach'} the ${num(s.interval_hours, 0)} h interval.`
      break
    case 'trade':
      text = `Trade ${s.removed_factories} production unit(s) for ${s.storages} storage facilit${s.storages === 1 ? 'y' : 'ies'}: `
        + `output ${num((s.output_share ?? 0) * 100, 0)} % of today, buffer ${hours(s.buffer_hours)}.`
      break
    case 'higher_tier': text = `No fit here. Produce the same product from the tier above: chain ${s.chain}.`; break
    default: text = `No change helps. Buffer stays ${hours(s.buffer_hours)}; consider a shorter collection interval.`
  }
  return (
    <Alert color={s.kind === 'covered' ? 'accent' : 'info'} py={6} title={`Storage suggestion: ${s.kind}`}>
      <Group justify="space-between" wrap="nowrap">
        <Text size="sm">{text}</Text>
        {s.design && <Button size="xs" onClick={() => onApply(s.design as PiDesign)}>Apply suggested design</Button>}
      </Group>
    </Alert>
  )
}

export default function PlannerDesignTools(p: Props) {
  const { data: meta } = usePiMeta()
  const [ways, setWays] = useState<PiWays | null>(null)
  const [storage, setStorage] = useState<PiStorageSuggestion | null>(null)
  const [counts, setCounts] = useState<Record<number, number | string>>({})
  const [launchpads, setLaunchpads] = useState<number | string>(1)
  const [storages, setStorages] = useState<number | string>(0)
  const [mixed, setMixed] = useState<PiMixedP2 | null>(null)
  const [mixedTemplate, setMixedTemplate] = useState<PiGeneratePayload | null>(null)

  const needBits = () => {
    if (!p.planetBits) throw new Error('Choose a planet or planet type first')
    return p.planetBits
  }
  const needProduct = () => {
    if (!p.chain || p.productId === null) throw new Error('Choose a chain and a product first')
    return { chain: p.chain, product_type_id: p.productId }
  }

  const waysM = useMutation({
    mutationFn: () => piApi.ways({ ...needBits(), product_type_id: needProduct().product_type_id }),
    onSuccess: setWays, onError: fail('Ways to build failed'),
  })
  const storageM = useMutation({
    mutationFn: () => piApi.storageSuggestion({
      ...needBits(), ...needProduct(), interval_hours: p.intervalHours,
      design: p.currentDesign ? coreOf(p.currentDesign) : undefined,
    }),
    onSuccess: setStorage, onError: fail('Storage suggestion failed'),
  })
  const growM = useMutation({
    mutationFn: () => piApi.grow({
      ...needBits(), ...needProduct(), yield_per_head: p.yieldPerHead,
      design: p.currentDesign ? coreOf(p.currentDesign) : undefined,
    }),
    onSuccess: (res) => p.onUseDesign(coreOf(res.design)), onError: fail('Grow to supply failed'),
  })

  const assignments = () => {
    const out: Record<number, number> = {}
    for (const [k, v] of Object.entries(counts)) if (Number(v) > 0) out[Number(k)] = Number(v)
    return out
  }
  const mixedM = useMutation({
    mutationFn: () => {
      const a = assignments()
      if (Object.keys(a).length === 0) throw new Error('Assign at least one factory to a P2 product')
      return piApi.mixedP2({ ...needBits(), assignments: a, launchpads: Number(launchpads) || 1, storages: Number(storages) || 0 })
    },
    onSuccess: (res) => { setMixed(res); setMixedTemplate(null) }, onError: fail('Mixed P2 failed'),
  })
  const mixedGen = useMutation({
    mutationFn: () => {
      if (!mixed) throw new Error('Evaluate the mix first')
      const a = assignments()
      const top = Object.entries(a).sort((x, y) => y[1] - x[1])[0]
      return piApi.generateLayout({
        ...needBits(), chain: 'P1-P2', product_type_id: Number(top[0]), design: coreOf(mixed.evaluation.design),
      })
    },
    onSuccess: setMixedTemplate, onError: fail('Template generation failed'),
  })

  const isExtraction = !!p.chain && p.chain.startsWith('P0')
  const p2Products = (meta?.products ?? []).filter((x) => x.tier === 2 && x.chains.includes('P1-P2'))
  const useRow = (r: PiWayRow) => r.evaluation && p.onUseDesign(coreOf(r.evaluation.design))

  return (
    <Stack>
      <SectionTitle>Design tools</SectionTitle>
      <Group>
        <Button size="xs" variant="default" disabled={(p.productTier ?? 0) < 2} loading={waysM.isPending}
          onClick={() => waysM.mutate()} title="Every split of the inputs into made here / hauled in (P2-P4 products)">
          Ways to build
        </Button>
        <Button size="xs" variant="default" loading={storageM.isPending} disabled={!p.currentDesign}
          onClick={() => storageM.mutate()}>Storage suggestion</Button>
        <Button size="xs" variant="default" loading={growM.isPending} disabled={!isExtraction || !p.currentDesign}
          onClick={() => growM.mutate()} title="Add factories while the output keeps rising (extraction chains)">
          Grow to supply
        </Button>
      </Group>

      {storage && <StorageResult s={storage} onApply={p.onUseDesign} />}

      {ways && (
        <Paper withBorder p="sm">
          <Stack gap="xs">
            <Text fw={600}>Ways to build {ways.product.name}</Text>
            {ways.variants.length > 0 && <WayTable rows={ways.variants} partial={false} onUse={useRow} />}
            {ways.partial.length > 0 && (
              <>
                <Text size="sm" c="dimmed">Partial sourcing: one P1 extracted here, the rest hauled in</Text>
                <WayTable rows={ways.partial} partial onUse={useRow} />
              </>
            )}
          </Stack>
        </Paper>
      )}

      {p.chain === 'P1-P2' && (
        <Paper withBorder p="sm">
          <Stack gap="xs">
            <Text fw={600}>Mixed P2 (several P2 products on one planet)</Text>
            <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }}>
              {p2Products.map((x) => (
                <NumberInput key={x.type_id} size="xs" label={x.name} min={0} max={60} placeholder="0"
                  value={counts[x.type_id] ?? ''} onChange={(v) => setCounts({ ...counts, [x.type_id]: v })} />
              ))}
            </SimpleGrid>
            <Group>
              <NumberInput size="xs" label="Launchpads" w={100} min={1} max={10} value={launchpads} onChange={setLaunchpads} />
              <NumberInput size="xs" label="Storage facilities" w={130} min={0} max={10} value={storages} onChange={setStorages} />
              <Button size="xs" loading={mixedM.isPending} onClick={() => mixedM.mutate()}>Evaluate mix</Button>
              <Button size="xs" variant="default" disabled={!mixed} loading={mixedGen.isPending}
                onClick={() => mixedGen.mutate()}>Generate template</Button>
            </Group>
            {mixed && (
              <Stack gap="xs">
                <Group>
                  {!mixed.evaluation.fits && <Badge color="danger">Does not fit CPU/power</Badge>}
                  <VerdictBadge worthIt={mixed.economics.worth_it} reason={mixed.economics.reason} />
                </Group>
                <SimpleGrid cols={{ base: 2, sm: 4 }}>
                  <Stat label="Output / h" value={num(mixed.evaluation.effective_product_per_hour, 1)} />
                  <Stat label="Profit" value={iskPerDay(mixed.economics.profit_per_day)} />
                  <Stat label="Setup cost" value={isk(mixed.evaluation.setup_isk)} />
                  <Stat label="Buffer" value={bufferText(mixed.evaluation.buffer_hours)} />
                </SimpleGrid>
                {mixed.evaluation.notes.map((n, i) => <Text key={i} size="sm" c="dimmed">{n}</Text>)}
              </Stack>
            )}
            {mixedTemplate && (
              <Group>
                <Text size="sm">Template generated ({mixedTemplate.analysis.ok ? 'valid' : 'has errors'}).</Text>
                <CopyButton value={mixedTemplate.template_json}>
                  {({ copied, copy }) => <Button size="xs" variant="default" onClick={copy}>{copied ? 'Copied' : 'Copy template'}</Button>}
                </CopyButton>
                <Button size="xs" onClick={() => p.onOpenInEditor(mixedTemplate)}>Open in editor</Button>
              </Group>
            )}
          </Stack>
        </Paper>
      )}
    </Stack>
  )
}
