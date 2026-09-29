import type { MailReceivedBy, MailRow } from '../../../api/types'

export const ARCHIVE_KEY = ['char-mail', 'archive']

// The server merges characters within one response; "Load more" then adds
// pages for only the characters that still have more, so the same mail can
// arrive again from a different character. Union by mail_id (received_by
// merged per character) and keep newest first.
export function mergeMailRows(rows: readonly MailRow[]): MailRow[] {
  const byId = new Map<number, MailRow>()
  for (const row of rows) {
    const existing = byId.get(row.mail_id)
    if (!existing) {
      byId.set(row.mail_id, { ...row, received_by: [...row.received_by] })
      continue
    }
    const seen = new Set(existing.received_by.map((r) => r.character_id))
    const added: MailReceivedBy[] = row.received_by.filter((r) => !seen.has(r.character_id))
    existing.received_by = [...existing.received_by, ...added]
    existing.is_read = existing.received_by.every((r) => r.is_read)
  }
  return [...byId.values()].sort((a, b) => {
    const ta = a.timestamp ? new Date(a.timestamp).getTime() : 0
    const tb = b.timestamp ? new Date(b.timestamp).getTime() : 0
    return tb - ta || b.mail_id - a.mail_id
  })
}

// Live mail is never indexed server-side, so this filters what is already
// loaded (sender, subject, recipient names). Archived characters additionally
// get real full-text search through the API.
export function filterMailRows(rows: readonly MailRow[], needle: string): MailRow[] {
  const q = needle.trim().toLowerCase()
  if (!q) return [...rows]
  return rows.filter((r) =>
    r.subject.toLowerCase().includes(q)
    || (r.from_name ?? '').toLowerCase().includes(q)
    || r.recipients.some((x) => (x.name ?? '').toLowerCase().includes(q)),
  )
}

export function backfillLabel(state: string, headers: number, bodies: number): string {
  if (state === 'off') return 'Not archived'
  const counts = `${headers} mails, ${bodies} with text`
  switch (state) {
    case 'running': return `Archiving… ${counts}`
    case 'done': return `Archived: ${counts}`
    case 'interrupted': return `Interrupted (${counts}) - Refresh archive resumes it`
    case 'error': return `Stopped by an error (${counts})`
    default: return `Waiting to start (${counts})`
  }
}
