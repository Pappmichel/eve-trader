/**
 * EVE industry runs on UTC. The Settings ``datetime-local`` field is labeled
 * "EVE time (UTC)": values are stored as ISO UTC and shown/edited without a
 * browser-local timezone shift.
 */

/** Stored ISO datetime → ``datetime-local`` value in UTC. */
export function toUtcInputValue(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}

/** ``datetime-local`` value interpreted as UTC → ISO string (or null). */
export function fromUtcInputValue(local: string): string | null {
  const trimmed = local.trim()
  if (!trimmed) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(trimmed)
  if (!m) return null
  const iso = `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:00.000Z`
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toISOString()
}

/** Format an ISO datetime for display in EVE time (UTC). */
export function formatEveUtc(iso: string | null): string {
  if (!iso) return 'not set'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
}
