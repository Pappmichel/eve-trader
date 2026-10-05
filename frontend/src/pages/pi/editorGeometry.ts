// Pure geometry helpers for the PI layout editor. A pin position is (La, Lo):
// La = polar angle (colatitude, radians), Lo = longitude. The map is an
// equirectangular projection around the layout's centre.

export const MIN_SPACING_RAD = 0.012
export const ROUTE_COLORS = ['#4dabf7', '#f783ac', '#b197fc', '#ffd43b', '#63e6be', '#ffa94d', '#74c0fc', '#e599f7']

export interface GeoPoint { la: number; lo: number }
export interface Point { x: number; y: number }

export interface Projection {
  cLo: number
  cosLa: number // sin(mean La): longitude scale at the centre
  cLa: number
  scale: number // pixels per unit
  offX: number
  offY: number
}

// Great-circle angle between two (colatitude, longitude) points.
export function greatCircleAngle(a: GeoPoint, b: GeoPoint): number {
  const c = Math.cos(a.la) * Math.cos(b.la) + Math.sin(a.la) * Math.sin(b.la) * Math.cos(a.lo - b.lo)
  return Math.acos(Math.min(1, Math.max(-1, c)))
}

// Indices (0-based) of pins closer than the minimum spacing to `p`, skipping `skip`.
export function tooClose(p: GeoPoint, pins: GeoPoint[], skip = -1): number[] {
  const out: number[] = []
  pins.forEach((q, i) => {
    if (i !== skip && greatCircleAngle(p, q) < MIN_SPACING_RAD) out.push(i)
  })
  return out
}

// Fit all points into width x height (with padding), keeping the aspect ratio.
export function makeProjection(points: GeoPoint[], width: number, height: number, pad = 40): Projection {
  const pts = points.length ? points : [{ la: Math.PI / 2, lo: 0 }]
  const cLa = pts.reduce((s, p) => s + p.la, 0) / pts.length
  const cLo = pts.reduce((s, p) => s + p.lo, 0) / pts.length
  const cosLa = Math.sin(cLa)
  const xs = pts.map((p) => (p.lo - cLo) * cosLa)
  const ys = pts.map((p) => p.la - cLa)
  const spanX = Math.max(Math.max(...xs) - Math.min(...xs), 0.05)
  const spanY = Math.max(Math.max(...ys) - Math.min(...ys), 0.05)
  const scale = Math.min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY)
  const midX = (Math.max(...xs) + Math.min(...xs)) / 2
  const midY = (Math.max(...ys) + Math.min(...ys)) / 2
  return { cLo, cosLa, cLa, scale, offX: width / 2 - midX * scale, offY: height / 2 - midY * scale }
}

export function project(p: GeoPoint, pr: Projection): Point {
  return { x: (p.lo - pr.cLo) * pr.cosLa * pr.scale + pr.offX, y: (p.la - pr.cLa) * pr.scale + pr.offY }
}

export function unproject(pt: Point, pr: Projection): GeoPoint {
  return {
    la: (pt.y - pr.offY) / pr.scale + pr.cLa,
    lo: (pt.x - pr.offX) / (pr.cosLa * pr.scale) + pr.cLo,
  }
}

export const round5 = (v: number): number => Math.round(v * 1e5) / 1e5

// Load ratio -> colour (green < 70 %, orange < 100 %, red over).
export function loadColor(load: number, capacity: number): string {
  const r = capacity > 0 ? load / capacity : load > 0 ? 2 : 0
  if (r < 0.7) return '#51cf66'
  if (r < 1) return '#ff922b'
  return '#fa5252'
}
