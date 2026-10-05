import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import {
  Badge, Button, CopyButton, Group, NumberInput, Paper, SegmentedControl, Select, SimpleGrid, Stack, Table,
  Text, Textarea, TextInput,
} from '@mantine/core'

import { piApi } from '../../api/client'
import type {
  PiDesign, PiGeneratePayload, PiPlan, PiPlanet, PiPlannerBody, PiPlannerResult,
} from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { notify } from '../../notify'
import { isk, pct } from '../../format'
import {
  AnalysisView, Bar, CC_OPTIONS, PlanetPicker, QueryError, RatesTable, SHAPES, SectionTitle, Stat,
  VerdictBadge, ZONE_OPTIONS, bufferText, iskPerDay, num, usePiMeta,
} from './common'

type NumField = number | string

interface Prefill {
  chain?: string
  product_type_id?: number
  planet_type_id?: number
  plan?: PiPlan
}

interface EditDesign {
  factories: Array<[number, number]>
  ecus: Array<[number, number]>
  launchpads: number
  storages: number
}

const optNum = (v: NumField): number | undefined => (v === '' ? undefined : Number(v))

function coreDesign(d: PiPlannerResult['evaluation']['design']): PiDesign {
  return {
    chain: d.chain, product_type_id: d.product_type_id, planet_type_id: d.planet_type_id, cc_level: d.cc_level,
    factories: d.factories, ecus: d.ecus, launchpads: d.launchpads, storages: d.storages,
  }
}

export default function Planner() {
  const location = useLocation()
  const prefill = (location.state as { prefill?: Prefill } | null)?.prefill
  const { data: meta, error: metaError } = usePiMeta()

  const [chain, setChain] = useState<string | null>(prefill?.plan?.design.chain ?? prefill?.chain ?? null)
  const [productId, setProductId] = useState<string | null>(
    String(prefill?.plan?.design.product_type_id ?? prefill?.product_type_id ?? '') || null)
  const [mode, setMode] = useState<'type' | 'planet'>(prefill?.plan?.planet_id ? 'planet' : 'type')
  const [planet, setPlanet] = useState<PiPlanet | null>(null)
  const [planetTypeId, setPlanetTypeId] = useState<string | null>(
    String(prefill?.plan?.planet_type_id ?? prefill?.planet_type_id ?? '') || null)
  const [radius, setRadius] = useState<NumField>(prefill?.plan?.radius_km ?? '')
  const [cc, setCc] = useState<string>(String(prefill?.plan?.design.cc_level ?? 5))
  const [zone, setZone] = useState<string | null>(null)
  const [ownerTax, setOwnerTax] = useState<NumField>(
    prefill?.plan?.owner_tax_rate != null ? prefill.plan.owner_tax_rate * 100 : '')
  const [freight, setFreight] = useState<NumField>(prefill?.plan?.freight_per_m3 ?? '')
  const [yieldHead, setYieldHead] = useState<NumField>(prefill?.plan?.yield_override ?? '')
  const [programH, setProgramH] = useState<NumField>('')
  const [intervalH, setIntervalH] = useState<NumField>('')

  const [result, setResult] = useState<PiPlannerResult | null>(null)
  const [edit, setEdit] = useState<EditDesign | null>(prefill?.plan ? {
    factories: prefill.plan.design.factories, ecus: prefill.plan.design.ecus,
    launchpads: prefill.plan.design.launchpads, storages: prefill.plan.design.storages,
  } : null)
  const [shape, setShape] = useState<string>('standard')
  const [generated, setGenerated] = useState<PiGeneratePayload | null>(null)
  const [name, setName] = useState(prefill?.plan?.name ?? '')

  const products = useMemo(
    () => (meta?.products ?? []).filter((p) => !chain || p.chains.includes(chain)),
    [meta, chain])

  // Switching chain invalidates a product the new chain cannot make.
  useEffect(() => {
    if (productId && meta && !products.some((p) => String(p.type_id) === productId)) setProductId(null)
  }, [products, productId, meta])

  const body = (design?: EditDesign | null): PiPlannerBody | null => {
    if (!chain || !productId) return null
    if (mode === 'planet' ? !planet : !planetTypeId) return null
    const b: PiPlannerBody = {
      chain, product_type_id: Number(productId), cc_level: Number(cc),
      owner_tax_rate: ownerTax === '' ? undefined : Number(ownerTax) / 100,
      freight_per_m3: optNum(freight), yield_per_head: optNum(yieldHead),
      program_hours: optNum(programH), interval_hours: optNum(intervalH),
    }
    if (mode === 'planet' && planet) b.planet_id = planet.planet_id
    else {
      b.planet_type_id = Number(planetTypeId)
      b.radius_km = optNum(radius)
      if (zone) b.zone = zone
    }
    if (design) b.design = design
    return b
  }

  const fail = (title: string) => (e: unknown) =>
    notify({ title, message: e instanceof Error ? e.message : String(e), color: 'danger' })

  const evaluate = useMutation({
    mutationFn: (design: EditDesign | null) => {
      const b = body(design)
      if (!b) throw new Error('Choose a chain, a product and a planet or planet type first')
      return piApi.planner(b)
    },
    onSuccess: (res, design) => {
      setResult(res)
      setGenerated(null)
      if (!design) {
        const d = res.evaluation.design
        setEdit({ factories: d.factories, ecus: d.ecus, launchpads: d.launchpads, storages: d.storages })
      }
    },
    onError: fail('Planner failed'),
  })

  const generate = useMutation({
    mutationFn: () => {
      const b = body(result ? edit : null)
      if (!b) throw new Error('Choose a chain, a product and a planet or planet type first')
      return piApi.generateLayout({ ...b, shape: shape === 'standard' ? undefined : shape })
    },
    onSuccess: setGenerated,
    onError: fail('Template generation failed'),
  })

  const saveTemplate = useAction('Save template', (p: { template: unknown; name: string }) =>
    piApi.saveTemplate({ template: p.template, name: p.name, source: 'generated' }), [['pi', 'templates']])
  const savePlan = useAction('Save plan', (p: Record<string, unknown>) => piApi.savePlan(p), [['pi', 'plans']])

  const planCore = (): Record<string, unknown> | null => {
    if (!result || !edit) return null
    const d = { ...coreDesign(result.evaluation.design), ...edit }
    return {
      name: name.trim() || `${result.product.name} (${result.chain})`,
      planet_id: result.planet.planet_id, planet_type_id: result.planet.planet_type_id,
      radius_km: result.planet.radius_km, design: d,
      owner_tax_rate: ownerTax === '' ? null : Number(ownerTax) / 100,
      freight_per_m3: optNum(freight) ?? null, yield_override: optNum(yieldHead) ?? null,
    }
  }

  const ev = result?.evaluation
  const eco = result?.economics

  return (
    <Stack>
      {metaError ? <QueryError error={metaError} /> : null}

      <Paper withBorder p="md">
        <Stack gap="sm">
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Select label="Chain" data={meta?.chains ?? []} value={chain} onChange={setChain} />
            <Select label="Product" searchable data={products.map((p) => ({ value: String(p.type_id), label: `${p.name} (P${p.tier})` }))}
              value={productId} onChange={setProductId} disabled={!chain} />
          </SimpleGrid>

          <SegmentedControl value={mode} onChange={(v) => setMode(v as 'type' | 'planet')}
            data={[{ value: 'type', label: 'Planet type + radius' }, { value: 'planet', label: 'Real planet' }]} maw={360} />
          {mode === 'planet' ? (
            <PlanetPicker onChange={(p) => setPlanet(p)} />
          ) : (
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              <Select label="Planet type" value={planetTypeId} onChange={setPlanetTypeId}
                data={(meta?.planet_types ?? []).map((t) => ({ value: String(t.type_id), label: t.name }))} />
              <NumberInput label="Radius (km)" placeholder="median of the type" min={50} max={200000}
                value={radius} onChange={setRadius} />
              <Select label="Security zone" clearable placeholder="Default from Settings" data={ZONE_OPTIONS}
                value={zone} onChange={setZone} />
            </SimpleGrid>
          )}

          <SimpleGrid cols={{ base: 1, xs: 2, sm: 3 }}>
            <Select label="Command Center level" data={CC_OPTIONS} value={cc} onChange={(v) => v && setCc(v)} allowDeselect={false} />
            <NumberInput label="Owner customs tax" suffix="%" min={0} max={100} placeholder="Settings default"
              value={ownerTax} onChange={setOwnerTax} />
            <NumberInput label="Freight (ISK/m³)" min={0} placeholder="Settings default" value={freight} onChange={setFreight} />
            <NumberInput label="Yield per head (P0/h)" min={0} placeholder="zone default" value={yieldHead} onChange={setYieldHead} />
            <NumberInput label="Program (hours)" min={1} max={336} placeholder="Settings default" value={programH} onChange={setProgramH} />
            <NumberInput label="Collection interval (hours)" min={1} max={336} placeholder="Settings default"
              value={intervalH} onChange={setIntervalH} />
          </SimpleGrid>

          <Group>
            <Button onClick={() => evaluate.mutate(null)} loading={evaluate.isPending}>Find best design</Button>
          </Group>
        </Stack>
      </Paper>

      {result && ev && eco && edit && (
        <Stack>
          <Group gap="sm">
            <Text fw={700} size="lg">{result.product.name}</Text>
            <Badge variant="outline">{result.chain}</Badge>
            <Badge variant="outline">
              {result.planet.name ?? result.planet.planet_type}, {num(result.planet.radius_km, 0)} km
            </Badge>
            <Badge variant="outline">{result.zone}</Badge>
            <VerdictBadge worthIt={eco.worth_it} reason={eco.reason} />
            {!ev.fits && <Badge color="danger">Does not fit CPU/power</Badge>}
          </Group>
          <Text size="xs" c="dimmed">
            Assumptions: yield {num(result.assumptions.yield_per_head, 0)}/head/h
            (effective {num(result.assumptions.effective_yield_per_head, 0)}),
            program {num(result.assumptions.program_hours, 0)} h, interval {num(result.assumptions.interval_hours, 0)} h,
            customs tax {pct(result.assumptions.tax_rate)}, freight {isk(result.assumptions.freight_per_m3)}/m³.
          </Text>

          <SectionTitle>Design</SectionTitle>
          <Table withRowBorders={false} verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr><Table.Th>Structure</Table.Th><Table.Th w={140}>Count / heads</Table.Th><Table.Th>Utilisation</Table.Th></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {ev.design.factories_named.map((f, i) => (
                <Table.Tr key={f.type_id}>
                  <Table.Td>Factory: {f.name}{f.tier !== null ? ` (P${f.tier})` : ''}</Table.Td>
                  <Table.Td>
                    <NumberInput size="xs" aria-label={`Factories ${f.name}`} min={0} max={100} value={edit.factories[i]?.[1] ?? 0}
                      onChange={(v) => setEdit({ ...edit, factories: edit.factories.map((x, j) => (j === i ? [x[0], Number(v) || 0] : x)) })} />
                  </Table.Td>
                  <Table.Td>{f.utilization !== undefined ? pct(f.utilization) : '–'}</Table.Td>
                </Table.Tr>
              ))}
              {ev.design.ecus_named.map((x, i) => (
                <Table.Tr key={x.type_id}>
                  <Table.Td>Extractor: {x.name}</Table.Td>
                  <Table.Td>
                    <NumberInput size="xs" aria-label={`Heads ${x.name}`} min={1} max={10} value={edit.ecus[i]?.[1] ?? 1}
                      onChange={(v) => setEdit({ ...edit, ecus: edit.ecus.map((e, j) => (j === i ? [e[0], Number(v) || 1] : e)) })} />
                  </Table.Td>
                  <Table.Td>heads</Table.Td>
                </Table.Tr>
              ))}
              <Table.Tr>
                <Table.Td>Launchpads</Table.Td>
                <Table.Td><NumberInput size="xs" aria-label="Launchpads" min={1} max={10} value={edit.launchpads}
                  onChange={(v) => setEdit({ ...edit, launchpads: Number(v) || 1 })} /></Table.Td>
                <Table.Td />
              </Table.Tr>
              <Table.Tr>
                <Table.Td>Storage facilities</Table.Td>
                <Table.Td><NumberInput size="xs" aria-label="Storage facilities" min={0} max={10} value={edit.storages}
                  onChange={(v) => setEdit({ ...edit, storages: Number(v) || 0 })} /></Table.Td>
                <Table.Td />
              </Table.Tr>
            </Table.Tbody>
          </Table>
          <Group>
            <Button variant="default" size="xs" onClick={() => evaluate.mutate(edit)} loading={evaluate.isPending}>
              Re-evaluate edited design
            </Button>
            <Button variant="subtle" size="xs" onClick={() => evaluate.mutate(null)}>Reset to best design</Button>
          </Group>

          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Bar label="CPU" used={ev.cpu_used} capacity={ev.cpu_capacity} />
            <Bar label="Power" used={ev.power_used} capacity={ev.power_capacity} />
          </SimpleGrid>

          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <Stat label="Links" value={`${ev.links.count} (${num(ev.links.km, 1)} km, level ${ev.links.level})`} />
            <Stat label="Buffer vs interval" value={`${bufferText(ev.buffer_hours)} vs ${num(result.assumptions.interval_hours, 0)} h`}
              hint="The buffer should cover the collection interval" />
            <Stat label="Setup cost" value={isk(ev.setup_isk)} />
            <Stat label="Idle factories" value={ev.idle_factories} />
            <Stat label="Product / h" value={num(ev.product_per_hour, 1)} />
            <Stat label="Effective / h" value={`${num(ev.effective_product_per_hour, 1)} (${pct(ev.effective_factor)})`} />
            <Stat label="Imports" value={`${num(ev.import_m3_per_hour, 1)} m³/h`} />
            <Stat label="Exports" value={`${num(ev.export_m3_per_hour, 1)} m³/h`} />
          </SimpleGrid>

          <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }}>
            <RatesTable title="Extracted" rates={ev.extracted} />
            <RatesTable title="Produced" rates={ev.produced} />
            <RatesTable title="Imports" rates={ev.imports} />
            <RatesTable title="Exports" rates={ev.exports} />
          </SimpleGrid>
          {ev.notes.map((n, i) => <Text key={i} size="sm" c="dimmed">{n}</Text>)}

          <SectionTitle>Economics (per day)</SectionTitle>
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <Stat label="Revenue" value={iskPerDay(eco.revenue_per_day)} />
            <Stat label="Inputs" value={iskPerDay(-eco.input_cost_per_day)} />
            <Stat label="Export tax" value={iskPerDay(-eco.export_tax_per_day)} />
            <Stat label="Import tax" value={iskPerDay(-eco.import_tax_per_day)} />
            <Stat label="Freight" value={iskPerDay(-eco.freight_per_day)} />
            <Stat label="Setup (amortised)" value={iskPerDay(-eco.setup_per_day)} />
            <Stat label="Profit" value={iskPerDay(eco.profit_per_day)} />
            <Stat label="Output" value={`${num(eco.output_units_per_day, 0)} units/day`} />
            <Stat label="Interactions / week" value={num(eco.interactions_per_week, 1)} />
            <Stat label="ISK / interaction" value={isk(eco.isk_per_interaction)} />
            <Stat label="Haul" value={`${num(eco.haul_m3_per_week, 0)} m³/week`} />
            <Stat label="Market share" value={pct(eco.market_share)} />
          </SimpleGrid>
          {eco.missing_prices.length > 0 && (
            <Text size="sm" c="warn">No market price for: {eco.missing_prices.map((m) => m.name).join(', ')}</Text>
          )}

          <SectionTitle>Template and plan</SectionTitle>
          <Group align="flex-end">
            <Select label="Shape" w={160} allowDeselect={false} data={SHAPES} value={shape} onChange={(v) => v && setShape(v)} />
            <Button onClick={() => generate.mutate()} loading={generate.isPending}>Generate template</Button>
            <TextInput label="Name" w={260} value={name} onChange={(e) => setName(e.currentTarget.value)}
              placeholder={`${result.product.name} (${result.chain})`} />
            <Button variant="default" loading={savePlan.isPending} onClick={() => {
              const p = planCore()
              if (p) savePlan.mutate(p)
            }}>Save as plan</Button>
          </Group>

          {generated && (
            <Stack>
              <Group>
                <Text fw={600}>Template ({generated.shape ?? 'standard'})</Text>
                <CopyButton value={generated.template_json}>
                  {({ copied, copy }) => <Button size="xs" variant="default" onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>}
                </CopyButton>
                <Button size="xs" variant="default" loading={saveTemplate.isPending} onClick={() =>
                  saveTemplate.mutate({ template: generated.template, name: name.trim() || `${result.product.name} (${result.chain})` })}>
                  Save template
                </Button>
              </Group>
              <Textarea readOnly autosize minRows={4} maxRows={12} value={generated.template_json}
                aria-label="Template JSON" styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }} />
              {generated.notes.map((n, i) => <Text key={i} size="sm" c="dimmed">{n}</Text>)}
              <AnalysisView analysis={generated.analysis} />
            </Stack>
          )}
        </Stack>
      )}
    </Stack>
  )
}

