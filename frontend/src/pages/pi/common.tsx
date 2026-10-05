import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useDebouncedValue } from '@mantine/hooks'
import {
  Alert, Badge, Group, Loader, Progress, Select, SimpleGrid, Stack, Table, Text, TextInput,
} from '@mantine/core'

import { piApi } from '../../api/client'
import type {
  PiAnalysis, PiNamedRate, PiPlanet, PiSystemHit, PiSystemPlanets, PiZone,
} from '../../api/types'
import { isk, pct } from '../../format'

// Label of a zone value (for "Settings default (Null-sec)" placeholders).
export function zoneLabel(zone: string | undefined | null): string {
  return ZONE_OPTIONS.find((z) => z.value === zone)?.label ?? '?'
}

// The PI settings, e.g. to show which default zone a page falls back to.
export function usePiSettings() {
  return useQuery({ queryKey: ['pi', 'settings'], queryFn: piApi.settings, staleTime: 60_000 }).data
}

export const ZONE_OPTIONS: Array<{ value: PiZone; label: string }> = [
  { value: 'highsec', label: 'High-sec' },
  { value: 'lowsec', label: 'Low-sec' },
  { value: 'nullsec', label: 'Null-sec' },
  { value: 'wormhole', label: 'Wormhole' },
]

export const CC_OPTIONS = [0, 1, 2, 3, 4, 5].map((l) => ({ value: String(l), label: `CC level ${l}` }))

export const SHAPES = ['standard', 'star', 'plus', 'grid', 'diamond', 'spiral', 'ring']

const REASON_LABELS: Record<string, string> = {
  tax: 'Customs tax',
  freight: 'Freight',
  input_cost: 'Input cost',
  setup: 'Setup cost',
  extraction_low: 'Extraction too low',
  market_thin: 'Market too thin',
  below_threshold: 'Below your threshold',
  no_price: 'No market price',
}

export function reasonLabel(reason: string | null | undefined): string {
  if (!reason) return ''
  return REASON_LABELS[reason] ?? reason
}

export function VerdictBadge({ worthIt, reason }: { worthIt: boolean; reason: string | null }) {
  if (worthIt) return <Badge color="accent" variant="light">Worth it</Badge>
  return (
    <Badge color="danger" variant="light" title={reasonLabel(reason)}>
      Not worth it{reason ? `: ${reasonLabel(reason)}` : ''}
    </Badge>
  )
}

export function num(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  return v.toLocaleString('en-US', { maximumFractionDigits: digits })
}

// Signed percent for a fraction (0.05 -> "+5.0%").
export function signedPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  return `${v > 0 ? '+' : ''}${pct(v)}`
}

export function iskPerDay(v: number | null | undefined): string {
  return v === null || v === undefined ? '–' : `${isk(v)}/day`
}

export function usePiMeta() {
  return useQuery({ queryKey: ['pi', 'meta'], queryFn: piApi.meta, staleTime: 5 * 60_000 })
}

// Backend errors (e.g. "PI data missing - run the SDE refresh in Admin first")
// arrive as the Error message; show them instead of a blank page.
export function QueryError({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return <Alert color="danger" title="Could not load">{message}</Alert>
}

export function Loading() {
  return <Group justify="center" p="xl"><Loader color="accent" /></Group>
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <Text size="sm" fw={600} c="dimmed" tt="uppercase" mt="sm">{children}</Text>
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div title={hint}>
      <Text size="xs" c="dimmed">{label}</Text>
      <Text fw={600}>{value}</Text>
    </div>
  )
}

// System search -> pick one. `onPick` gets the chosen system hit.
export function SystemSearch({ onPick, label = 'Solar system' }: {
  onPick: (hit: PiSystemHit | null) => void
  label?: string
}) {
  const [query, setQuery] = useState('')
  const [debounced] = useDebouncedValue(query, 300)
  const q = debounced.trim()
  const { data, isFetching, error } = useQuery({
    queryKey: ['pi', 'systems', q], queryFn: () => piApi.searchSystems(q), enabled: q.length >= 2,
  })
  const hits = useMemo(() => data ?? [], [data])
  const [picked, setPicked] = useState<string | null>(null)
  // One hit, or a hit whose name is exactly what was typed: take it without
  // making the user open the dropdown.
  useEffect(() => {
    if (picked !== null || hits.length === 0) return
    const exact = hits.find((h) => h.name.toLowerCase() === q.toLowerCase())
    const auto = exact ?? (hits.length === 1 ? hits[0] : undefined)
    if (auto) { setPicked(String(auto.solar_system_id)); onPick(auto) }
  }, [hits, q, picked, onPick])
  return (
    <Stack gap="xs">
      <TextInput label={label} placeholder="Type at least 2 letters" value={query}
        rightSection={isFetching ? <Loader size="xs" /> : null}
        onChange={(e) => { setQuery(e.currentTarget.value); setPicked(null); onPick(null) }} />
      {error ? <QueryError error={error} /> : null}
      {hits.length > 0 && (
        <Select aria-label={`${label} results`} placeholder={`${hits.length} match(es), pick one`} value={picked}
          data={hits.map((h) => ({
            value: String(h.solar_system_id),
            label: `${h.name} (${num(h.security, 1)}, ${h.zone}, ${h.planet_count} planets)`,
          }))}
          onChange={(v) => { setPicked(v); onPick(hits.find((h) => String(h.solar_system_id) === v) ?? null) }} />
      )}
    </Stack>
  )
}

// System search + planet list of that system.
export function PlanetPicker({ onChange }: {
  onChange: (planet: PiPlanet | null, system: PiSystemPlanets | null) => void
}) {
  const [systemId, setSystemId] = useState<number | null>(null)
  const { data, error, isFetching } = useQuery({
    queryKey: ['pi', 'system', systemId], queryFn: () => piApi.systemPlanets(systemId as number),
    enabled: systemId !== null,
  })
  return (
    <Stack gap="xs">
      <SystemSearch onPick={(h) => { setSystemId(h?.solar_system_id ?? null); onChange(null, null) }} />
      {isFetching && <Loader size="xs" />}
      {error ? <QueryError error={error} /> : null}
      {data && (
        <Select label={`Planet in ${data.name} (${data.zone})`} placeholder="Pick a planet"
          data={data.planets.map((p) => ({
            value: String(p.planet_id), label: `${p.name} - ${p.planet_type ?? p.planet_type_id}, ${num(p.radius_km, 0)} km`,
          }))}
          onChange={(v) => onChange(data.planets.find((p) => String(p.planet_id) === v) ?? null, data)} />
      )}
    </Stack>
  )
}

export function Bar({ label, used, capacity }: { label: string; used: number; capacity: number }) {
  const ratio = capacity > 0 ? used / capacity : used > 0 ? 2 : 0
  return (
    <div>
      <Group justify="space-between">
        <Text size="xs" c="dimmed">{label}</Text>
        <Text size="xs" c={ratio > 1 ? 'danger' : undefined}>{num(used, 0)} / {num(capacity, 0)}</Text>
      </Group>
      <Progress value={Math.min(100, ratio * 100)} color={ratio > 1 ? 'danger' : ratio > 0.9 ? 'warn' : 'accent'} />
    </div>
  )
}

export function RatesTable({ title, rates }: { title: string; rates: PiNamedRate[] }) {
  if (rates.length === 0) return null
  return (
    <div>
      <Text size="xs" c="dimmed" fw={600} mb={4}>{title}</Text>
      <Table withRowBorders={false} verticalSpacing={2}>
        <Table.Tbody>
          {rates.map((r) => (
            <Table.Tr key={r.type_id}>
              <Table.Td>{r.name}{r.tier !== null && r.tier !== undefined ? ` (P${r.tier})` : ''}</Table.Td>
              <Table.Td ta="right">{num(r.per_hour, 1)} /h</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </div>
  )
}

export function bufferText(hours: number | null | undefined): string {
  return hours === null || hours === undefined ? 'unlimited' : `${num(hours, 1)} h`
}

const SEVERITY_COLOR: Record<string, string> = { error: 'danger', warning: 'warn', info: 'info' }

// Analysis of a layout/template (validate, generate, retarget).
export function AnalysisView({ analysis }: { analysis: PiAnalysis }) {
  return (
    <Stack gap="sm">
      <Group gap="sm">
        <Badge color={analysis.ok ? 'accent' : 'danger'} variant="light">
          {analysis.ok ? 'Template is valid' : 'Template has errors'}
        </Badge>
        {analysis.product_name && <Badge variant="outline">{analysis.product_name}</Badge>}
        {analysis.chain && <Badge variant="outline">{analysis.chain}</Badge>}
      </Group>

      {analysis.findings.length > 0 && (
        <Stack gap={4}>
          {analysis.findings.map((f, i) => (
            <Alert key={`${f.code}-${i}`} color={SEVERITY_COLOR[f.severity] ?? 'gray'} py={6}
              title={f.severity.toUpperCase()}>
              {f.message}
            </Alert>
          ))}
        </Stack>
      )}

      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Bar label="CPU" used={analysis.cpu_used} capacity={analysis.cpu_capacity} />
        <Bar label="Power" used={analysis.power_used} capacity={analysis.power_capacity} />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 2, sm: 4 }}>
        <Stat label="Buffer" value={bufferText(analysis.buffer_hours)} hint="How long the launchpad/storage buffer lasts" />
        <Stat label="Storage" value={`${num(analysis.storage_m3, 0)} m³`} />
        <Stat label="Imports" value={`${num(analysis.import_m3_per_hour, 1)} m³/h`} />
        <Stat label="Exports" value={`${num(analysis.export_m3_per_hour, 1)} m³/h`} />
        <Stat label="Setup cost" value={isk(analysis.setup_isk)} />
        <Stat label="Link CPU / power" value={`${num(analysis.link_cpu, 0)} / ${num(analysis.link_power, 0)}`} />
        <Stat label="Links" value={analysis.links.length} />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }}>
        <RatesTable title="Extracted" rates={analysis.extracted} />
        <RatesTable title="Produced" rates={analysis.produced} />
        <RatesTable title="Imports" rates={analysis.imports} />
        <RatesTable title="Exports" rates={analysis.exports} />
      </SimpleGrid>

      {analysis.links.length > 0 && (
        <div>
          <Text size="xs" c="dimmed" fw={600} mb={4}>Link loads</Text>
          <Table withRowBorders={false} verticalSpacing={2}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>#</Table.Th><Table.Th ta="right">Length</Table.Th>
                <Table.Th ta="right">Load (m³/h)</Table.Th><Table.Th ta="right">Capacity (m³/h)</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {analysis.links.map((l, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{i + 1}</Table.Td>
                  <Table.Td ta="right">{num(l.km, 1)} km</Table.Td>
                  <Table.Td ta="right" c={l.load_m3h > l.capacity_m3h ? 'danger' : undefined}>{num(l.load_m3h, 1)}</Table.Td>
                  <Table.Td ta="right">{num(l.capacity_m3h, 1)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
      )}
    </Stack>
  )
}
