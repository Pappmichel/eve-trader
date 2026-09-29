import type { MailDraftRecipient, MailOpened } from '../../../api/types'
import { dateTime } from '../../../format'
import { sanitizeMailBody } from '../../../mailHtml'

// ESI's hard limits for a mail (docs/CHARACTER_MANAGEMENT_PLAN.md phase 4);
// the server checks them again, these only give early feedback.
export const MAX_RECIPIENTS = 50
export const MAX_SUBJECT = 1000
export const MAX_BODY = 10000

export interface DraftRecipient extends MailDraftRecipient {
  /** Shown on the chip. */
  label: string
}

export interface ComposeDraft {
  /** The character to send as; null = let the user pick. */
  fromCharacterId: number | null
  recipients: DraftRecipient[]
  subject: string
  body: string
}

export const EMPTY_DRAFT: ComposeDraft = { fromCharacterId: null, recipients: [], subject: '', body: '' }

export function replySubject(subject: string): string {
  return /^re:\s/i.test(subject) ? subject : `Re: ${subject}`
}

export function forwardSubject(subject: string): string {
  return /^(fwd?|fw):\s/i.test(subject) ? subject : `Fwd: ${subject}`
}

// A mail body is HTML-ish; a quote in a plain-text editor needs the text. Goes
// through the same sanitizer as the reader, so nothing executable is ever
// parsed here either, then <br>/block ends become line breaks.
export function htmlToPlainText(html: string): string {
  const root = document.createElement('div')
  root.innerHTML = sanitizeMailBody(html)
  root.querySelectorAll('br').forEach((br) => br.replaceWith('\n'))
  root.querySelectorAll('p, div, li, blockquote').forEach((el) => el.append('\n'))
  return (root.textContent ?? '').replace(/\n{3,}/g, '\n\n').trim()
}

export function quoteBody(mail: MailOpened, header: string): string {
  const text = htmlToPlainText(mail.body)
  const quoted = text.split('\n').map((line) => `> ${line}`).join('\n')
  return `\n\n${header}\n${quoted}`
}

function sender(mail: MailOpened): DraftRecipient[] {
  return mail.from_id === null ? [] : [{
    type: 'character', id: mail.from_id, name: mail.from_name ?? undefined,
    label: mail.from_name ?? `#${mail.from_id}`,
  }]
}

// Reply goes to the sender; reply-all also to everyone else the mail was
// addressed to (characters, corporations, alliances and mailing lists),
// minus the character replying and duplicates. The reply is sent as the
// character that received the mail.
export function replyDraft(mail: MailOpened, all: boolean): ComposeDraft {
  const recipients: DraftRecipient[] = [...sender(mail)]
  if (all) {
    for (const r of mail.recipients) {
      if (r.recipient_type === 'character' && r.recipient_id === mail.character_id) continue
      recipients.push({
        type: r.recipient_type, id: r.recipient_id, name: r.name ?? undefined,
        label: r.name ?? `#${r.recipient_id}`,
      })
    }
  }
  const seen = new Set<string>()
  const unique = recipients.filter((r) => {
    const key = `${r.type}:${r.id}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  return {
    fromCharacterId: mail.character_id,
    recipients: unique,
    subject: replySubject(mail.subject),
    body: quoteBody(mail, `On ${dateTime(mail.timestamp)}, ${mail.from_name ?? 'someone'} wrote:`),
  }
}

export function forwardDraft(mail: MailOpened): ComposeDraft {
  return {
    fromCharacterId: mail.character_id,
    recipients: [],
    subject: forwardSubject(mail.subject),
    body: quoteBody(mail, `---------- Forwarded message ----------\nFrom: ${mail.from_name ?? 'someone'}\nDate: ${dateTime(mail.timestamp)}\nSubject: ${mail.subject}\n`),
  }
}

export function toRequestRecipients(recipients: readonly DraftRecipient[]): MailDraftRecipient[] {
  return recipients.map((r) => (r.id !== undefined
    ? { type: r.type, id: r.id, name: r.name }
    : { type: r.type || undefined, name: r.name ?? r.label }))
}

export function draftProblems(d: ComposeDraft): string[] {
  const out: string[] = []
  if (d.fromCharacterId === null) out.push('Pick the character to send as.')
  if (d.recipients.length === 0) out.push('Add at least one recipient.')
  if (d.recipients.length > MAX_RECIPIENTS) out.push(`At most ${MAX_RECIPIENTS} recipients.`)
  if (!d.subject.trim()) out.push('A mail needs a subject.')
  if (d.subject.length > MAX_SUBJECT) out.push(`The subject can be at most ${MAX_SUBJECT} characters.`)
  if (d.body.length > MAX_BODY) out.push(`The message can be at most ${MAX_BODY} characters (it has ${d.body.length}).`)
  return out
}
