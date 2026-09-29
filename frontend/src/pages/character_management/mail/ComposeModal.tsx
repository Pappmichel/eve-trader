import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Badge, Button, CloseButton, Group, Modal, Paper, Select, Stack, Text, Textarea, TextInput, UnstyledButton,
} from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, charMailApi } from '../../../api/client'
import type { MailRecipientHit } from '../../../api/types'
import { isk } from '../../../format'
import {
  draftProblems, MAX_BODY, MAX_SUBJECT, toRequestRecipients, type ComposeDraft, type DraftRecipient,
} from './mailCompose'

const TYPE_LABEL: Record<string, string> = {
  character: 'character', corporation: 'corp', alliance: 'alliance', mailing_list: 'list',
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}

export interface Sender { character_id: number; character_name: string }

// Compose / reply / forward. Sending goes through the server, which re-checks
// that the character has "Send mail" ticked AND a token with the scope, and
// never retries: an unknown outcome is reported as such, and the user is told to
// look in Sent before trying again.
export function ComposeModal({ opened, onClose, draft, senders }: {
  opened: boolean
  onClose: () => void
  draft: ComposeDraft
  senders: Sender[]
}) {
  const queryClient = useQueryClient()
  // The parent remounts this component (`key`) for every new draft, so state is
  // initialised once - deliberately no effect that re-syncs from props, which
  // would wipe what the user is typing on every parent render.
  const [d, setD] = useState<ComposeDraft>(() => ({
    ...draft,
    fromCharacterId: draft.fromCharacterId ?? (senders.length === 1 ? senders[0].character_id : null),
  }))
  const [typed, setTyped] = useState('')
  const debounced = useDebounced(typed.trim(), 300)

  const search = useQuery({
    queryKey: ['char-mail', 'recipients', d.fromCharacterId, debounced],
    queryFn: () => charMailApi.searchRecipients(d.fromCharacterId as number, debounced),
    enabled: opened && d.fromCharacterId !== null && debounced.length >= 3,
    staleTime: 60_000,
  })
  const hits: MailRecipientHit[] = useMemo(() => {
    const taken = new Set(d.recipients.map((r) => `${r.type}:${r.id}`))
    return (search.data?.results ?? []).filter((h) => !taken.has(`${h.type}:${h.id}`))
  }, [search.data, d.recipients])

  const problems = draftProblems(d)

  const addRecipient = (r: DraftRecipient) => {
    setD((prev) => (prev.recipients.some((x) => x.type === r.type && x.id === r.id && x.label === r.label)
      ? prev : { ...prev, recipients: [...prev.recipients, r] }))
    setTyped('')
  }

  const send = useMutation({
    mutationFn: (approvedCost: number) => charMailApi.send({
      from_character_id: d.fromCharacterId as number,
      recipients: toRequestRecipients(d.recipients),
      subject: d.subject.trim(),
      body: d.body,
      approved_cost: approvedCost,
    }),
    onSuccess: (result) => {
      if (!result.sent) {
        // ESI wants a CSPA charge approved first. Nothing was sent.
        modals.openConfirmModal({
          title: 'Recipient charges a fee to receive mail',
          children: (
            <Text size="sm">
              One recipient has set a CSPA charge: sending costs {isk(result.cost)}. Send anyway?
            </Text>
          ),
          labels: { confirm: `Send for ${isk(result.cost)}`, cancel: 'Cancel' },
          onConfirm: () => send.mutate(Math.ceil(result.cost)),
        })
        return
      }
      notifications.show({ title: 'Mail sent', message: `To ${result.recipients.map((r) => r.name ?? `#${r.recipient_id}`).join(', ')}`, color: 'accent' })
      queryClient.invalidateQueries({ queryKey: ['char-mail'] })
      onClose()
    },
    onError: (err: unknown) => {
      notifications.show({
        title: 'Could not send the mail', color: 'danger',
        message: err instanceof ApiError ? err.message : String(err),
      })
    },
  })

  return (
    <Modal opened={opened} onClose={onClose} title="New mail" size="lg" closeOnClickOutside={false}>
      <Stack gap="sm">
        {senders.length === 0 ? (
          <Alert color="warn" variant="light">
            No character can send mail yet. Tick &quot;Send mail&quot; for a character on the Characters page and
            re-authorize it.
          </Alert>
        ) : (
          <Select
            label="From"
            data={senders.map((s) => ({ value: String(s.character_id), label: s.character_name }))}
            value={d.fromCharacterId === null ? null : String(d.fromCharacterId)}
            onChange={(v) => setD((p) => ({ ...p, fromCharacterId: v === null ? null : Number(v) }))}
            allowDeselect={false}
          />
        )}

        <div>
          <Text size="sm" fw={500} mb={4}>To</Text>
          <Group gap={4} mb={4}>
            {d.recipients.map((r, i) => (
              <Badge
                key={`${r.type}:${r.id}:${r.label}:${i}`} variant="light" size="lg" tt="none"
                rightSection={(
                  <CloseButton
                    size="xs" aria-label={`Remove recipient ${r.label}`}
                    onClick={() => setD((p) => ({ ...p, recipients: p.recipients.filter((_, j) => j !== i) }))}
                  />
                )}
              >
                {r.label}{r.type ? ` · ${TYPE_LABEL[r.type] ?? r.type}` : ''}
              </Badge>
            ))}
          </Group>
          <TextInput
            aria-label="Add recipient"
            placeholder="Type a name, pick a suggestion or press Enter"
            value={typed}
            onChange={(e) => setTyped(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && typed.trim()) {
                e.preventDefault()
                addRecipient({ name: typed.trim(), label: typed.trim() })
              }
            }}
          />
          {hits.length > 0 && (
            <Paper withBorder mt={4} p={4} aria-label="Recipient suggestions">
              {hits.map((h) => (
                <UnstyledButton
                  key={`${h.type}:${h.id}`} w="100%" p={4}
                  onClick={() => addRecipient({ type: h.type, id: h.id, name: h.name, label: h.name })}
                >
                  <Group justify="space-between">
                    <Text size="sm">{h.name}</Text>
                    <Badge size="xs" variant="light" color="gray">{TYPE_LABEL[h.type]}</Badge>
                  </Group>
                </UnstyledButton>
              ))}
            </Paper>
          )}
          <Text size="xs" c="dimmed" mt={2}>
            Exact names are looked up when you send. A mailing list must be one this character is subscribed to.
          </Text>
        </div>

        <TextInput
          label="Subject"
          value={d.subject}
          onChange={(e) => {
            // read before the updater runs: React nulls currentTarget once the handler returns
            const value = e.currentTarget.value
            setD((p) => ({ ...p, subject: value }))
          }}
          description={`${d.subject.length}/${MAX_SUBJECT}`}
        />
        <Textarea
          label="Message"
          autosize minRows={8} maxRows={18}
          value={d.body}
          onChange={(e) => {
            const value = e.currentTarget.value
            setD((p) => ({ ...p, body: value }))
          }}
          description={`${d.body.length}/${MAX_BODY}`}
        />

        {problems.length > 0 && senders.length > 0 && (
          <Text size="xs" c="dimmed">{problems.join(' ')}</Text>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button
            disabled={problems.length > 0 || senders.length === 0}
            loading={send.isPending}
            onClick={() => send.mutate(0)}
          >
            Send
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
