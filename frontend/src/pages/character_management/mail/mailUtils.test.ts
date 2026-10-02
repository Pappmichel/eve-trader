import { describe, expect, it } from 'vitest'

import type { MailRow } from '../../../api/types'
import { backfillLabel, filterMailRows, mergeMailRows } from './mailUtils'

function mail(id: number, over: Partial<MailRow> = {}): MailRow {
  return {
    mail_id: id, from_id: 9, from_name: 'Sender', subject: `Subject ${id}`,
    timestamp: `2026-09-${String(id).padStart(2, '0')}T10:00:00Z`, is_read: true, recipients: [],
    received_by: [{ character_id: 1, character_name: 'Alice', is_read: true, labels: [1], archived: false }],
    ...over,
  }
}

describe('mergeMailRows', () => {
  it('sorts newest first and unions the characters that received the same mail', () => {
    const bobCopy = mail(5, {
      is_read: false,
      received_by: [{ character_id: 2, character_name: 'Bob', is_read: false, labels: [1], archived: true }],
    })
    const merged = mergeMailRows([mail(3), mail(5), bobCopy, mail(9)])
    expect(merged.map((m) => m.mail_id)).toEqual([9, 5, 3])
    const five = merged.find((m) => m.mail_id === 5)!
    expect(five.received_by.map((r) => r.character_name)).toEqual(['Alice', 'Bob'])
    expect(five.is_read).toBe(false)        // unread for Bob -> unread overall
  })

  it('does not duplicate a character that comes back again', () => {
    const merged = mergeMailRows([mail(5), mail(5)])
    expect(merged).toHaveLength(1)
    expect(merged[0].received_by).toHaveLength(1)
  })

  it('does not mutate its input', () => {
    const a = mail(5)
    mergeMailRows([a, mail(5, { received_by: [{ character_id: 2, character_name: 'Bob', is_read: true, labels: [], archived: false }] })])
    expect(a.received_by).toHaveLength(1)
  })
})

describe('filterMailRows', () => {
  const rows = [
    mail(1, { subject: 'Fleet doctrine', from_name: 'Alice Alt' }),
    mail(2, { subject: 'Hello', recipients: [{ recipient_id: 7, recipient_type: 'mailing_list', name: 'Logistics list' }] }),
  ]
  it('matches subject, sender and recipient names case-insensitively; empty keeps all', () => {
    expect(filterMailRows(rows, 'DOCTRINE').map((m) => m.mail_id)).toEqual([1])
    expect(filterMailRows(rows, 'alice').map((m) => m.mail_id)).toEqual([1])
    expect(filterMailRows(rows, 'logistics').map((m) => m.mail_id)).toEqual([2])
    expect(filterMailRows(rows, '  ')).toHaveLength(2)
    expect(filterMailRows(rows, 'nothing')).toEqual([])
  })
})

describe('backfillLabel', () => {
  it('describes each state', () => {
    expect(backfillLabel('off', 0, 0)).toBe('Not archived')
    expect(backfillLabel('running', 120, 40)).toContain('Archiving')
    expect(backfillLabel('done', 120, 120)).toBe('Archived: 120 mails, 120 with text')
    expect(backfillLabel('interrupted', 1, 0)).toContain('resumes it')
    expect(backfillLabel('error', 1, 0)).toContain('error')
  })
})
