import { describe, expect, it } from 'vitest'

import type { MailOpened } from '../../../api/types'
import {
  draftProblems, EMPTY_DRAFT, forwardDraft, forwardSubject, htmlToPlainText, replyDraft, replySubject,
  toRequestRecipients,
} from './mailCompose'

function mail(over: Partial<MailOpened> = {}): MailOpened {
  return {
    mail_id: 5, from_id: 9, from_name: 'Sender', subject: 'Fleet', timestamp: '2026-09-28T18:00:00Z',
    is_read: false, character_id: 1, archived: false,
    body: 'Line one<br>Line two<script>alert(1)</script>',
    recipients: [
      { recipient_id: 1, recipient_type: 'character', name: 'Me' },
      { recipient_id: 2, recipient_type: 'character', name: 'Friend' },
      { recipient_id: 700, recipient_type: 'corporation', name: 'Corp' },
      { recipient_id: 7, recipient_type: 'mailing_list', name: 'List' },
    ],
    received_by: [{ character_id: 1, character_name: 'Me', is_read: false, labels: [1], archived: false }],
    ...over,
  }
}

describe('subjects', () => {
  it('adds Re:/Fwd: once', () => {
    expect(replySubject('Fleet')).toBe('Re: Fleet')
    expect(replySubject('RE: Fleet')).toBe('RE: Fleet')
    expect(forwardSubject('Fleet')).toBe('Fwd: Fleet')
    expect(forwardSubject('Fwd: Fleet')).toBe('Fwd: Fleet')
    expect(forwardSubject('FW: Fleet')).toBe('FW: Fleet')
  })
})

describe('htmlToPlainText', () => {
  it('keeps line breaks, drops markup and scripts', () => {
    expect(htmlToPlainText('Hi <b>there</b><br>bye<script>alert(1)</script>')).toBe('Hi there\nbye')
    expect(htmlToPlainText('<p>a</p><p>b</p>')).toBe('a\nb')
  })
})

describe('replyDraft', () => {
  it('replies to the sender only, as the character that received the mail, quoting the body', () => {
    const d = replyDraft(mail(), false)
    expect(d.fromCharacterId).toBe(1)
    expect(d.recipients.map((r) => [r.type, r.id])).toEqual([['character', 9]])
    expect(d.subject).toBe('Re: Fleet')
    expect(d.body).toContain('Sender wrote:')
    expect(d.body).toContain('> Line one\n> Line two')
    expect(d.body).not.toContain('alert')
  })

  it('reply-all adds the other recipients but not the replying character, and no duplicates', () => {
    const m = mail({ from_id: 2, from_name: 'Friend' })          // the sender is also on the To line
    const d = replyDraft(m, true)
    expect(d.recipients.map((r) => `${r.type}:${r.id}`)).toEqual([
      'character:2', 'corporation:700', 'mailing_list:7',
    ])
  })

  it('copes with a mail without a known sender', () => {
    const d = replyDraft(mail({ from_id: null, from_name: null }), false)
    expect(d.recipients).toEqual([])
    expect(d.body).toContain('someone wrote:')
  })
})

describe('forwardDraft', () => {
  it('has no recipients and quotes the original with its header', () => {
    const d = forwardDraft(mail())
    expect(d.recipients).toEqual([])
    expect(d.subject).toBe('Fwd: Fleet')
    expect(d.body).toContain('Forwarded message')
    expect(d.body).toContain('Subject: Fleet')
    expect(d.body).toContain('> Line one')
    expect(d.fromCharacterId).toBe(1)
  })
})

describe('toRequestRecipients', () => {
  it('sends id+type for picked recipients and only a name for typed ones', () => {
    expect(toRequestRecipients([
      { type: 'character', id: 9, name: 'Sender', label: 'Sender' },
      { name: 'Some Corp', label: 'Some Corp' },
    ])).toEqual([
      { type: 'character', id: 9, name: 'Sender' },
      { type: undefined, name: 'Some Corp' },
    ])
  })
})

describe('draftProblems', () => {
  it('lists what is missing or too long', () => {
    expect(draftProblems(EMPTY_DRAFT)).toEqual([
      'Pick the character to send as.', 'Add at least one recipient.', 'A mail needs a subject.',
    ])
    const ok = { fromCharacterId: 1, recipients: [{ label: 'x', name: 'x' }], subject: 's', body: 'b' }
    expect(draftProblems(ok)).toEqual([])
    expect(draftProblems({ ...ok, body: 'x'.repeat(10001) })[0]).toMatch(/at most 10000/)
    expect(draftProblems({ ...ok, subject: 'x'.repeat(1001) })[0]).toMatch(/at most 1000/)
    expect(draftProblems({ ...ok, recipients: Array.from({ length: 51 }, (_, i) => ({ label: `${i}`, name: `${i}` })) })[0])
      .toMatch(/At most 50/)
  })
})
