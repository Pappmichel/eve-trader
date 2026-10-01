import { Alert, Button, ColorSwatch, Divider, Group, Select, Stack, Switch, Text, TextInput, Tooltip } from '@mantine/core'
import { modals } from '@mantine/modals'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notify } from '../../../notify'

import { ApiError, charMailApi } from '../../../api/client'
import type { MailArchiveRow } from '../../../api/types'
import { dateTime } from '../../../format'
import { ARCHIVE_KEY, LABEL_COLORS, backfillLabel } from './mailUtils'
import { useState } from 'react'

// Mail is read live and nothing is stored - unless a character's "Archive
// mail" switch is on here. Turning it off deletes that character's archive,
// which is the one and only way archived mail is ever removed.
function ArchiveRow({ row }: { row: MailArchiveRow }) {
  const queryClient = useQueryClient()
  const toggle = useMutation({
    mutationFn: (args: { enabled: boolean; confirm?: boolean }) =>
      charMailApi.setArchive({ character_id: row.character_id, enabled: args.enabled, confirm_delete: args.confirm }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['char-mail'] })
    },
    onError: (err: unknown) => {
      notify({
        title: 'Mail archive', color: 'danger',
        message: err instanceof ApiError ? err.message : String(err),
      })
    },
  })

  const setEnabled = (enabled: boolean) => {
    if (enabled || row.counts.headers === 0) {
      toggle.mutate({ enabled })
      return
    }
    modals.openConfirmModal({
      title: `Delete ${row.character_name}'s archived mail?`,
      children: (
        <Text size="sm">
          Turning the archive off deletes {row.counts.headers} archived mail(s) of {row.character_name} from
          this app&apos;s database. The mail stays in the game; you can archive it again later, which
          re-downloads it.
        </Text>
      ),
      labels: { confirm: 'Delete archive', cancel: 'Keep' },
      confirmProps: { color: 'danger' },
      onConfirm: () => toggle.mutate({ enabled: false, confirm: true }),
    })
  }

  const blocked = !row.archive_enabled && (!row.shared || row.reauth_needed)
  return (
    <Stack gap={2} py="xs">
      <Group justify="space-between" wrap="nowrap">
        <div>
          <Text fw={600}>{row.character_name}</Text>
          <Text size="xs" c="dimmed">{backfillLabel(row.backfill_state, row.counts.headers, row.counts.bodies)}</Text>
        </div>
        <Switch
          aria-label={`Archive mail for ${row.character_name}`}
          label="Archive mail"
          checked={row.archive_enabled}
          disabled={blocked || toggle.isPending}
          onChange={(e) => setEnabled(e.currentTarget.checked)}
        />
      </Group>
      {blocked && (
        <Text size="xs" c="warn">
          {!row.shared
            ? 'Not shared with Mail - tick it on the Characters page first.'
            : 'Needs a re-authorize with the mail scope (Characters page).'}
        </Text>
      )}
      {row.archive_enabled && !row.shared && (
        <Text size="xs" c="warn">Not shared with Mail any more: the archive is kept but hidden until it is shared again.</Text>
      )}
      {row.error && <Text size="xs" c="danger">{row.error}</Text>}
      {row.archive_enabled && (
        <Text size="xs" c="dimmed">Last refreshed {row.last_refresh_at ? dateTime(row.last_refresh_at) : 'never'}</Text>
      )}
    </Stack>
  )
}

// Custom label management for a character that may organize mail. Labels live
// in the game (ESI); this only creates/deletes them there. Built-in folders
// cannot be touched.
function LabelManager() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['char-mail', 'folders'], queryFn: charMailApi.folders })
  const [name, setName] = useState('')
  const [color, setColor] = useState<string>('#ffffff')
  const [characterId, setCharacterId] = useState<string | null>(null)
  const chars = (data?.characters ?? []).filter((c) => c.capabilities?.organize === 'ready')
  const active = chars.find((c) => String(c.character_id) === characterId) ?? chars[0]
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['char-mail'] })
  const fail = (title: string) => (err: unknown) => notify({
    title, color: 'danger', message: err instanceof ApiError ? err.message : String(err),
  })
  const create = useMutation({
    mutationFn: () => charMailApi.createLabel({ character_id: active.character_id, name: name.trim(), color }),
    onSuccess: () => { setName(''); refresh() },
    onError: fail('Could not create the label'),
  })
  const remove = useMutation({
    mutationFn: (labelId: number) => charMailApi.deleteLabel(active.character_id, labelId),
    onSuccess: refresh,
    onError: fail('Could not delete the label'),
  })
  if (chars.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        Labels: tick &quot;Organize mail&quot; for a character on the Characters page (and re-authorize) to create
        and delete labels here.
      </Text>
    )
  }
  const custom = (active?.labels ?? []).filter((l) => !l.system)
  return (
    <Stack gap="xs">
      <Text fw={600}>Labels</Text>
      {chars.length > 1 && (
        <Select
          aria-label="Label character" size="xs" allowDeselect={false}
          data={chars.map((c) => ({ value: String(c.character_id), label: c.character_name }))}
          value={String(active.character_id)} onChange={setCharacterId}
        />
      )}
      {custom.length === 0 && <Text size="xs" c="dimmed">No custom labels for {active.character_name}.</Text>}
      {custom.map((l) => (
        <Group key={l.label_id} justify="space-between" wrap="nowrap">
          <Group gap="xs">
            <ColorSwatch size={14} color={l.color ?? '#ffffff'} />
            <Text size="sm">{l.name}</Text>
          </Group>
          <Button
            size="compact-xs" variant="subtle" color="danger" loading={remove.isPending}
            aria-label={`Delete label ${l.name}`}
            onClick={() => modals.openConfirmModal({
              title: `Delete the label "${l.name}"?`,
              children: <Text size="sm">Mail carrying this label keeps existing, it just loses the label.</Text>,
              labels: { confirm: 'Delete label', cancel: 'Keep' },
              confirmProps: { color: 'danger' },
              onConfirm: () => remove.mutate(l.label_id),
            })}
          >
            Delete
          </Button>
        </Group>
      ))}
      <Group gap="xs" wrap="nowrap" align="flex-end">
        <TextInput
          aria-label="New label name" placeholder="New label" size="xs" value={name}
          onChange={(e) => setName(e.currentTarget.value)} maxLength={40} style={{ flex: 1 }}
        />
        <Select
          aria-label="Label colour" size="xs" w={110} allowDeselect={false} value={color}
          onChange={(v) => setColor(v ?? '#ffffff')} data={[...LABEL_COLORS]}
          leftSection={<ColorSwatch size={12} color={color} />}
        />
        <Tooltip label="Enter a name" disabled={name.trim().length > 0}>
          <span>
            <Button size="xs" disabled={!name.trim()} loading={create.isPending} onClick={() => create.mutate()}>Add</Button>
          </span>
        </Tooltip>
      </Group>
    </Stack>
  )
}

export function MailSettings() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ARCHIVE_KEY, queryFn: charMailApi.archive,
    refetchInterval: (query) =>
      query.state.data?.characters.some((c) => c.backfill_state === 'running') ? 2000 : false,
  })
  const refresh = useMutation({
    mutationFn: () => charMailApi.refreshArchive(),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['char-mail'] }),
  })
  const rows = data?.characters ?? []
  const anyArchived = rows.some((r) => r.archive_enabled)
  return (
    <Stack gap="sm">
      <Alert color="info" variant="light" title="Live by default">
        Mail is read live from ESI and nothing about it is stored on this server. Switch on
        &quot;Archive mail&quot; for a character to keep a searchable copy in this app&apos;s database
        (headers and text, no size limit). Switching it off deletes that copy.
      </Alert>
      {isLoading && <Text size="sm" c="dimmed">Loading…</Text>}
      {rows.length === 0 && !isLoading && <Text size="sm" c="dimmed">No ESI characters registered yet.</Text>}
      {rows.map((r) => <ArchiveRow key={r.character_id} row={r} />)}
      <Divider />
      <LabelManager />
      {anyArchived && (
        <Group>
          <Button size="xs" variant="default" loading={refresh.isPending} onClick={() => refresh.mutate()}>
            Refresh archive
          </Button>
          <Text size="xs" c="dimmed">Also resumes an interrupted download.</Text>
        </Group>
      )}
    </Stack>
  )
}
