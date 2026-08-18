// Only used for *display*; sorting always happens on the real numeric field
// (see components/DataTable.tsx), which is the structural fix for the old
// Streamlit sort bug (formatted strings baked into cells sorted as text).

const nf = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const nf1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })

export function isk(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '–'
  return `${nf.format(Math.round(value))} ISK`
}

export function qty(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '–'
  return nf.format(Math.round(value))
}

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '–'
  return `${nf1.format(value * 100)}%`
}

const dtf = new Intl.DateTimeFormat('en-US', { dateStyle: 'short', timeStyle: 'short' })

// `isoUtc`: ISO-8601 string as stored by the backend - always UTC, but not
// always *labeled* UTC: some call sites use datetime.now(timezone.utc).
// isoformat() (has a "+00:00" suffix, unambiguous), others use the older
// datetime.utcnow().isoformat() (naive - no offset at all, e.g. trading's
// esi_sync_state "trading" scope via actions.now_ts()). The JS Date
// constructor treats an offset-less string as *local* time, not UTC - would
// silently mis-render by the browser's UTC offset for any such value. Append
// "Z" when there's no offset already, so every value here is always
// interpreted as UTC regardless of which backend convention produced it.
export function dateTime(isoUtc: string | null | undefined): string {
  if (!isoUtc) return 'never'
  const hasOffset = /[Zz]$|[+-]\d\d:\d\d$/.test(isoUtc)
  return dtf.format(new Date(hasOffset ? isoUtc : `${isoUtc}Z`))
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '–'
  if (seconds <= 0) return 'done'
  const totalMinutes = Math.floor(seconds / 60)
  const days = Math.floor(totalMinutes / (24 * 60))
  const hours = Math.floor((totalMinutes % (24 * 60)) / 60)
  const minutes = totalMinutes % 60
  const parts: string[] = []
  if (days) parts.push(`${days}d`)
  if (hours || days) parts.push(`${hours}h`)
  parts.push(`${minutes}m`)
  return parts.join(' ')
}
