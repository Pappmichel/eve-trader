import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useDebouncedValue } from '@mantine/hooks'
import {
  Alert, Badge, Button, CopyButton, Grid, Group, NumberInput, Paper, Select, Stack, Switch, Text, Textarea,
  TextInput,
} from '@mantine/core'

import { piApi } from '../../api/client'
import type { PiEditOp, PiLayoutPayload, PiTemplateJson } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { notify } from '../../notify'
import { AnalysisView, PlanetPicker, SectionTitle, num, usePiMeta } from './common'
import {
  ROUTE_COLORS, loadColor, makeProjection, project, round5, tooClose, unproject,
  type GeoPoint, type Point,
} from './editorGeometry'
import { setUnsavedGuard } from './unsavedGuard'

// What the page is opened with (router location.state).
export interface EditorOpenState {
  template: unknown
  template_json?: string
  planet_id?: number
  radius_km?: number
  name?: string
  source?: 'generated' | 'paste'
}

const W = 900
const H = 620
const KIND_OPTIONS = [
  { value: 'launchpad', label: 'Launchpad' },
  { value: 'storage', label: 'Storage facility' },
  { value: 'ecu', label: 'Extractor (ECU)' },
  { value: 'basic', label: 'Basic factory (P1)' },
  { value: 'advanced', label: 'Advanced factory (P2/P3)' },
  { value: 'high_tech', label: 'High-tech factory (P4)' },
]
const KIND_COLOR: Record<string, string> = {
  launchpad: '#339af0', storage: '#15aabf', ecu: '#fab005', basic: '#94d82d', advanced: '#e599f7', high_tech: '#ff8787',
}
const FACTORY_TIERS: Record<string, number[]> = { basic: [1], advanced: [2, 3], high_tech: [4] }

type Selection = { kind: 'pin' | 'link'; i: number } | null

function StructureIcon({ kind, r }: { kind: string | null; r: number }) {
  const fill = KIND_COLOR[kind ?? ''] ?? '#adb5bd'
  const common = { fill, stroke: '#212529', strokeWidth: 1.5 }
  switch (kind) {
    case 'launchpad': return <rect x={-r} y={-r} width={2 * r} height={2 * r} {...common} />
    case 'storage': return <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={r / 2} {...common} />
    case 'ecu': return <polygon points={`0,${-r * 1.2} ${r * 1.1},${r} ${-r * 1.1},${r}`} {...common} />
    case 'advanced': return <polygon points={`0,${-r * 1.2} ${r * 1.2},0 0,${r * 1.2} ${-r * 1.2},0`} {...common} />
    case 'high_tech':
      return <polygon points={[0, 1, 2, 3, 4, 5].map((k) => `${r * 1.15 * Math.cos(k * Math.PI / 3)},${r * 1.15 * Math.sin(k * Math.PI / 3)}`).join(' ')} {...common} />
    default: return <circle r={r} {...common} />
  }
}

export default function Editor() {
  const location = useLocation()
  const openState = location.state as EditorOpenState | null
  const { data: meta } = usePiMeta()

  const [hist, setHist] = useState<PiLayoutPayload[]>([])
  const [idx, setIdx] = useState(0)
  const [original, setOriginal] = useState<PiLayoutPayload | null>(null)
  const [baselineJson, setBaselineJson] = useState<string | null>(null)
  const [source, setSource] = useState<'generated' | 'paste'>(openState?.source ?? 'paste')
  const [name, setName] = useState(openState?.name ?? '')
  const [busy, setBusy] = useState(false)
  const [lastRefusal, setLastRefusal] = useState<string | null>(null)

  const [pasteText, setPasteText] = useState('')
  const [useRealPlanet, setUseRealPlanet] = useState(false)
  const [planetId, setPlanetId] = useState<number | null>(openState?.planet_id ?? null)
  const [radius, setRadius] = useState<number | string>(openState?.radius_km ?? '')

  const [sel, setSel] = useState<Selection>(null)
  const [hover, setHover] = useState<number | null>(null)
  const [drag, setDrag] = useState<{ i: number; pt: Point; bad: boolean } | null>(null)
  const [view, setView] = useState({ k: 1, tx: 0, ty: 0 })
  const [addKind, setAddKind] = useState<string>('basic')
  const [addProduct, setAddProduct] = useState<string | null>(null)
  const [addHeads, setAddHeads] = useState<number | string>(4)
  const [addMode, setAddMode] = useState(false)

  const seq = useRef(0)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const gesture = useRef<
    | { type: 'pin'; i: number; sx: number; sy: number; moved: boolean }
    | { type: 'pan'; sx: number; sy: number; tx: number; ty: number; moved: boolean }
    | null
  >(null)

  const cur = hist[idx] ?? null
  const tpl = cur?.template as unknown as PiTemplateJson | undefined
  const dirty = !!cur && cur.template_json !== baselineJson

  useEffect(() => {
    setUnsavedGuard(dirty ? () => true : null)
    const onUnload = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', onUnload)
    return () => { window.removeEventListener('beforeunload', onUnload); setUnsavedGuard(null) }
  }, [dirty])

  const geometry = useCallback(() => ({
    planet_id: useRealPlanet && planetId ? planetId : undefined,
    radius_km: !useRealPlanet && radius !== '' ? Number(radius) : undefined,
  }), [useRealPlanet, planetId, radius])

  const names = useMemo(() => {
    const m = new Map<number, string>()
    for (const p of meta?.products ?? []) m.set(p.type_id, p.name)
    for (const t of meta?.planet_types ?? []) for (const r of t.resources) m.set(r.type_id, r.name)
    return m
  }, [meta])

  // ---- opening
  const fail = (title: string) => (e: unknown) =>
    notify({ title, message: e instanceof Error ? e.message : String(e), color: 'danger' })

  const openTemplate = useCallback(async (template: unknown, extra?: Partial<EditorOpenState>) => {
    const my = ++seq.current
    setBusy(true)
    try {
      const geo = extra ? { planet_id: extra.planet_id, radius_km: extra.radius_km } : geometry()
      const res = await piApi.validateLayout({ template, ...geo })
      if (my !== seq.current) return
      setHist([res]); setIdx(0); setOriginal(res); setBaselineJson(res.template_json)
      setSel(null); setView({ k: 1, tx: 0, ty: 0 }); setLastRefusal(null)
    } catch (e) {
      if (my === seq.current) fail('Could not open the template')(e)
    } finally {
      if (my === seq.current) setBusy(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometry])

  const openedKey = useRef<string | null>(null)
  useEffect(() => {
    if (!openState?.template || openedKey.current === location.key) return
    openedKey.current = location.key
    if (openState.planet_id) { setUseRealPlanet(true); setPlanetId(openState.planet_id) }
    void openTemplate(openState.template_json ?? openState.template, openState)
  }, [location.key, openState, openTemplate])

  // ---- history
  const push = (res: PiLayoutPayload) => {
    setHist((h) => [...h.slice(0, idx + 1), res])
    setIdx((i) => i + 1)
    setSel(null)
  }

  const applyEdit = async (edit: PiEditOp) => {
    if (!cur || busy) return
    const my = ++seq.current
    setBusy(true)
    setLastRefusal(null)
    try {
      const res = await piApi.editLayout({ template: cur.template_json, edit, ...geometry() })
      if (my !== seq.current) return
      push(res)
    } catch (e) {
      if (my !== seq.current) return
      const msg = e instanceof Error ? e.message : String(e)
      setLastRefusal(msg) // the previous layout stays
      notify({ title: 'Edit refused', message: msg, color: 'warn' })
    } finally {
      if (my === seq.current) setBusy(false)
    }
  }

  // Re-analyse the current layout (Validate button / planet or radius change).
  // Stale answers are dropped by the request sequence number.
  const revalidate = useCallback(async () => {
    const at = idx
    const json = hist[at]?.template_json
    if (!json) return
    const my = ++seq.current
    try {
      const res = await piApi.validateLayout({ template: json, ...geometry() })
      if (my !== seq.current) return
      setHist((h) => h.map((x, i) => (i === at ? { ...x, analysis: res.analysis } : x)))
    } catch (e) {
      if (my === seq.current) fail('Validation failed')(e)
    }
  }, [hist, idx, geometry])

  const geoKey = JSON.stringify(geometry())
  const [debouncedGeo] = useDebouncedValue(geoKey, 250)
  const firstGeo = useRef(true)
  useEffect(() => {
    if (firstGeo.current) { firstGeo.current = false; return }
    void revalidate()
    // only a changed planet/radius re-runs it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedGeo])

  const undo = () => { if (idx > 0) { seq.current++; setIdx(idx - 1); setSel(null) } }
  const redo = () => { if (idx < hist.length - 1) { seq.current++; setIdx(idx + 1); setSel(null) } }
  const reset = () => {
    if (!original) return
    seq.current++
    setHist((h) => [...h.slice(0, idx + 1), original])
    setIdx(idx + 1)
    setSel(null)
  }

  const save = useAction('Save template', () =>
    piApi.saveTemplate({ template: (cur as PiLayoutPayload).template_json, name: name.trim() || undefined, source })
      .then((r) => { setBaselineJson((cur as PiLayoutPayload).template_json); return r }),
  [['pi', 'templates']])

  // ---- geometry
  const pins: GeoPoint[] = useMemo(() => (tpl?.P ?? []).map((p) => ({ la: p.La, lo: p.Lo })), [tpl])
  const pr = useMemo(() => makeProjection(pins, W, H, 30), [pins])
  const posOf = (i: number): Point => (drag && drag.i === i ? drag.pt : project(pins[i], pr))

  const toLocal = (e: { clientX: number; clientY: number }): Point | null => {
    const svg = svgRef.current
    const ctm = svg?.getScreenCTM()
    if (!svg || !ctm) return null
    const pt = svg.createSVGPoint()
    pt.x = e.clientX; pt.y = e.clientY
    const p = pt.matrixTransform(ctm.inverse())
    return { x: (p.x - view.tx) / view.k, y: (p.y - view.ty) / view.k }
  }
  const toSvg = (e: { clientX: number; clientY: number }): Point | null => {
    const svg = svgRef.current
    const ctm = svg?.getScreenCTM()
    if (!svg || !ctm) return null
    const pt = svg.createSVGPoint()
    pt.x = e.clientX; pt.y = e.clientY
    const p = pt.matrixTransform(ctm.inverse())
    return { x: p.x, y: p.y }
  }

  // wheel zoom needs a non-passive listener to stop the page scrolling
  const viewRef = useRef(view)
  viewRef.current = view
  const hasLayout = !!cur
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const ctm = svg.getScreenCTM()
      if (!ctm) return
      const pt = svg.createSVGPoint()
      pt.x = e.clientX; pt.y = e.clientY
      const p = pt.matrixTransform(ctm.inverse())
      const v = viewRef.current
      const k = Math.min(20, Math.max(0.3, v.k * (e.deltaY < 0 ? 1.15 : 1 / 1.15)))
      setView({ k, tx: p.x - ((p.x - v.tx) / v.k) * k, ty: p.y - ((p.y - v.ty) / v.k) * k })
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [hasLayout])

  const onPinDown = (e: React.PointerEvent, i: number) => {
    e.stopPropagation()
    if (busy) return
    svgRef.current?.setPointerCapture(e.pointerId)
    gesture.current = { type: 'pin', i, sx: e.clientX, sy: e.clientY, moved: false }
  }
  const onBgDown = (e: React.PointerEvent) => {
    const p = toSvg(e)
    if (!p) return
    svgRef.current?.setPointerCapture(e.pointerId)
    gesture.current = { type: 'pan', sx: p.x, sy: p.y, tx: view.tx, ty: view.ty, moved: false }
  }
  const onMove = (e: React.PointerEvent) => {
    const g = gesture.current
    if (!g) return
    if (g.type === 'pan') {
      const p = toSvg(e)
      if (!p) return
      if (Math.abs(p.x - g.sx) + Math.abs(p.y - g.sy) > 3) g.moved = true
      if (g.moved) setView((v) => ({ ...v, tx: g.tx + p.x - g.sx, ty: g.ty + p.y - g.sy }))
      return
    }
    if (Math.abs(e.clientX - g.sx) + Math.abs(e.clientY - g.sy) > 4) g.moved = true
    if (!g.moved) return
    const pt = toLocal(e)
    if (!pt) return
    const geo = unproject(pt, pr)
    setDrag({ i: g.i, pt, bad: tooClose(geo, pins, g.i).length > 0 })
  }
  const onUp = (e: React.PointerEvent) => {
    const g = gesture.current
    gesture.current = null
    svgRef.current?.releasePointerCapture?.(e.pointerId)
    if (!g) return
    if (g.type === 'pin') {
      const d = drag
      setDrag(null)
      if (!g.moved || !d) { setSel({ kind: 'pin', i: g.i }); return }
      const geo = unproject(d.pt, pr)
      if (d.bad) {
        notify({ title: 'Too close', message: 'Structures must be at least 0.012 rad apart. The move was not sent.', color: 'warn' })
        return
      }
      void applyEdit({ op: 'move', pin: g.i + 1, la: round5(geo.la), lo: round5(geo.lo) })
      return
    }
    if (g.moved) return
    // background click
    if (addMode) {
      const pt = toLocal(e)
      if (!pt) return
      const geo = unproject(pt, pr)
      if (tooClose(geo, pins).length > 0) {
        notify({ title: 'Too close', message: 'Pick a spot at least 0.012 rad from every other structure.', color: 'warn' })
        return
      }
      const isFactory = addKind in FACTORY_TIERS
      if ((isFactory || addKind === 'ecu') && !addProduct) {
        notify({ title: 'Choose a product', message: 'Pick the product (or P0 resource) first.', color: 'warn' })
        return
      }
      const op: PiEditOp = {
        op: 'add', kind: addKind, la: round5(geo.la), lo: round5(geo.lo),
        ...(addProduct && (isFactory || addKind === 'ecu') ? { product: Number(addProduct) } : {}),
        ...(addKind === 'ecu' ? { heads: Number(addHeads) || 1 } : {}),
      }
      void applyEdit(op)
    } else {
      setSel(null)
    }
  }

  // ---- derived drawing data
  const kinds = cur?.analysis.kinds ?? []
  const ringPins = useMemo(() => {
    const s = new Set<number>()
    for (const f of cur?.analysis.findings ?? []) for (const p of f.pins) s.add(p - 1)
    return s
  }, [cur])
  const commodityColor = useMemo(() => {
    const ids = [...new Set((tpl?.R ?? []).map((r) => r.T))].sort((a, b) => a - b)
    return new Map(ids.map((id, n) => [id, ROUTE_COLORS[n % ROUTE_COLORS.length]]))
  }, [tpl])
  const activePin = hover ?? (sel?.kind === 'pin' ? sel.i : null)
  const activeRoutes = activePin === null ? [] : (tpl?.R ?? []).filter((r) => r.P.includes(activePin + 1))

  const productOptions = useMemo(() => {
    if (addKind in FACTORY_TIERS) {
      return (meta?.products ?? []).filter((p) => FACTORY_TIERS[addKind].includes(p.tier))
        .map((p) => ({ value: String(p.type_id), label: `${p.name} (P${p.tier})` }))
    }
    if (addKind === 'ecu') {
      const t = meta?.planet_types.find((x) => x.type_id === tpl?.Pln)
      return (t?.resources ?? []).map((r) => ({ value: String(r.type_id), label: `${r.name} (P0)` }))
    }
    return []
  }, [addKind, meta, tpl])

  const selPinLabel = sel?.kind === 'pin' && tpl
    ? `#${sel.i + 1} ${kinds[sel.i] ?? 'structure'}${tpl.P[sel.i]?.S ? ` - ${names.get(tpl.P[sel.i].S as number) ?? tpl.P[sel.i].S}` : ''}`
    : null

  if (!cur || !tpl) {
    return (
      <Stack>
        <SectionTitle>Layout editor</SectionTitle>
        <Text size="sm" c="dimmed">
          Open a template from the Planner (Open in editor), from Templates, or paste an EVE PI template JSON here.
        </Text>
        <Textarea label="Template JSON" autosize minRows={5} maxRows={14} value={pasteText}
          onChange={(e) => setPasteText(e.currentTarget.value)} styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }} />
        <Group>
          <Button disabled={!pasteText.trim()} loading={busy} onClick={() => { setSource('paste'); void openTemplate(pasteText) }}>
            Open in editor
          </Button>
        </Group>
      </Stack>
    )
  }

  const L = tpl.L
  return (
    <Stack gap="sm">
      <Group gap="xs" align="flex-end">
        <Select label="Add" w={210} size="xs" data={KIND_OPTIONS} value={addKind} allowDeselect={false}
          onChange={(v) => { if (v) { setAddKind(v); setAddProduct(null) } }} />
        {(addKind in FACTORY_TIERS || addKind === 'ecu') && (
          <Select label={addKind === 'ecu' ? 'P0 resource' : 'Product'} w={210} size="xs" searchable data={productOptions}
            value={addProduct} onChange={setAddProduct} />
        )}
        {addKind === 'ecu' && (
          <NumberInput label="Heads" w={70} size="xs" min={1} max={10} value={addHeads} onChange={setAddHeads} />
        )}
        <Button size="xs" variant={addMode ? 'filled' : 'default'} onClick={() => setAddMode(!addMode)}>
          {addMode ? 'Click the map to place...' : 'Add structure'}
        </Button>
        <Button size="xs" variant="default" disabled={busy || sel?.kind !== 'pin'}
          onClick={() => sel && void applyEdit({ op: 'remove', pin: sel.i + 1 })}>Remove selected</Button>
        {sel?.kind === 'link' && L[sel.i] && (
          <Select label={`Link ${sel.i + 1} level`} w={110} size="xs" allowDeselect={false}
            data={[0, 1, 2, 3, 4, 5].map((l) => ({ value: String(l), label: `Level ${l}` }))}
            value={String(L[sel.i].Lv)}
            onChange={(v) => v !== null && void applyEdit({ op: 'link_level', link: (sel.i) + 1, level: Number(v) })} />
        )}
        <Button size="xs" variant="default" disabled={busy} onClick={() => void applyEdit({ op: 'route_storage' })}>Route storage</Button>
        <Button size="xs" variant="default" disabled={idx === 0 || busy} onClick={undo}>Undo</Button>
        <Button size="xs" variant="default" disabled={idx >= hist.length - 1 || busy} onClick={redo}>Redo</Button>
        <Button size="xs" variant="default" disabled={busy} onClick={reset}>Reset to original</Button>
        <Button size="xs" variant="default" disabled={busy} onClick={() => void revalidate()}>Validate</Button>
        {dirty && <Badge color="warn" variant="light">Unsaved edits</Badge>}
      </Group>
      {lastRefusal && <Alert color="warn" py={6} title="Edit refused (layout unchanged)">{lastRefusal}</Alert>}

      <Grid>
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Paper withBorder p={0} style={{ overflow: 'hidden' }}>
            <svg ref={svgRef} data-testid="pi-editor-map" viewBox={`0 0 ${W} ${H}`} width="100%"
              style={{ display: 'block', touchAction: 'none', cursor: addMode ? 'crosshair' : 'default', background: 'var(--mantine-color-body)' }}
              onPointerMove={onMove} onPointerUp={onUp}>
              <rect x={0} y={0} width={W} height={H} fill="transparent" onPointerDown={onBgDown} />
              <g transform={`translate(${view.tx} ${view.ty}) scale(${view.k})`}>
                {L.map((lk, i) => {
                  const a = lk.S - 1
                  const b = lk.D - 1
                  if (!pins[a] || !pins[b]) return null
                  const pa = posOf(a)
                  const pb = posOf(b)
                  const an = cur.analysis.links[i]
                  const color = an ? loadColor(an.load_m3h, an.capacity_m3h) : '#868e96'
                  const selected = sel?.kind === 'link' && sel.i === i
                  return (
                    <g key={i}>
                      <line x1={pa.x} y1={pa.y} x2={pb.x} y2={pb.y} stroke={color} strokeWidth={selected ? 5 : 2.5}
                        vectorEffect="non-scaling-stroke" opacity={0.9} />
                      <line x1={pa.x} y1={pa.y} x2={pb.x} y2={pb.y} stroke="transparent" strokeWidth={12}
                        vectorEffect="non-scaling-stroke" style={{ cursor: 'pointer' }}
                        onPointerDown={(e) => { e.stopPropagation(); setSel({ kind: 'link', i }) }}>
                        <title>{`Link ${i + 1}: level ${lk.Lv}${an ? `, ${num(an.km, 1)} km, ${num(an.load_m3h, 1)} / ${num(an.capacity_m3h, 1)} m³/h` : ''}`}</title>
                      </line>
                    </g>
                  )
                })}
                {activeRoutes.map((r, n) => (
                  <polyline key={`r${n}`} fill="none" stroke={commodityColor.get(r.T) ?? '#fff'} strokeWidth={5}
                    vectorEffect="non-scaling-stroke" opacity={0.75} strokeDasharray="8 4" pointerEvents="none"
                    points={r.P.filter((p) => pins[p - 1]).map((p) => { const q = posOf(p - 1); return `${q.x},${q.y}` }).join(' ')} />
                ))}
                {pins.map((_, i) => {
                  const p = posOf(i)
                  const bad = (drag?.i === i && drag.bad) || ringPins.has(i)
                  const isSel = sel?.kind === 'pin' && sel.i === i
                  const productId = tpl.P[i].S
                  const label = productId ? names.get(productId) ?? String(productId) : (kinds[i] ?? '')
                  return (
                    <g key={i} transform={`translate(${p.x} ${p.y})`} data-testid={`pin-${i + 1}`} style={{ cursor: 'grab' }}
                      onPointerDown={(e) => onPinDown(e, i)}
                      onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover((h) => (h === i ? null : h))}>
                      {bad && <circle r={15} fill="none" stroke="#fa5252" strokeWidth={3} data-testid="pin-ring" />}
                      {isSel && <circle r={12} fill="none" stroke="#ffffff" strokeWidth={1.5} strokeDasharray="3 2" />}
                      <StructureIcon kind={kinds[i] ?? null} r={8} />
                      <text y={22} textAnchor="middle" fontSize={10} fill="currentColor" pointerEvents="none">
                        {i + 1}{label ? ` ${label}` : ''}
                      </text>
                    </g>
                  )
                })}
              </g>
            </svg>
          </Paper>
          <Group gap="md" mt={4} wrap="wrap">
            {Object.entries(KIND_COLOR).map(([k, c]) => (
              <Group key={k} gap={4}><span style={{ width: 10, height: 10, background: c, display: 'inline-block' }} /><Text size="xs" c="dimmed">{k}</Text></Group>
            ))}
            <Text size="xs" c="dimmed">Links: green &lt; 70 %, orange &lt; 100 %, red over capacity. Wheel zooms, drag the background pans.</Text>
          </Group>
          {activeRoutes.length > 0 && (
            <Group gap="md" mt={4}>
              {activeRoutes.map((r, n) => (
                <Group key={n} gap={4}>
                  <span style={{ width: 14, height: 4, background: commodityColor.get(r.T), display: 'inline-block' }} />
                  <Text size="xs">{names.get(r.T) ?? r.T} ({num(r.Q, 0)}/cycle)</Text>
                </Group>
              ))}
            </Group>
          )}
          {selPinLabel && <Text size="xs" c="dimmed" mt={4}>Selected: {selPinLabel}</Text>}
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Stack gap="sm">
            <SectionTitle>Planet</SectionTitle>
            <Switch label="Check against a real planet" checked={useRealPlanet}
              onChange={(e) => setUseRealPlanet(e.currentTarget.checked)} />
            {useRealPlanet ? (
              <PlanetPicker onChange={(p) => setPlanetId(p?.planet_id ?? null)} />
            ) : (
              <NumberInput label="Planet radius (km)" w={220} size="xs" min={50} max={200000} value={radius} onChange={setRadius}
                placeholder={`${num(tpl.Diam / 2, 0)} (from the template)`} />
            )}

            <SectionTitle>Template</SectionTitle>
            <Group align="flex-end">
              <CopyButton value={cur.template_json}>
                {({ copied, copy }) => <Button size="xs" variant="default" onClick={copy}>{copied ? 'Copied' : 'Copy template'}</Button>}
              </CopyButton>
              <TextInput label="Name" size="xs" w={200} value={name} onChange={(e) => setName(e.currentTarget.value)}
                placeholder="Template name" />
              <Button size="xs" loading={save.isPending} onClick={() => save.mutate()}>Save to library</Button>
            </Group>
          </Stack>
        </Grid.Col>
      </Grid>
      <AnalysisView analysis={cur.analysis} />
    </Stack>
  )
}
