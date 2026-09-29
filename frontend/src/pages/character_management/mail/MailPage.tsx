import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Badge, Box, Button, Container, Divider, Drawer, Grid, Group, Loader, NavLink, ScrollArea,
  Stack, Text, TextInput, Title, Tooltip, UnstyledButton,
} from '@mantine/core'
import { IconArrowLeft, IconSettings } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { charMailApi } from '../../../api/client'
import type { MailFolders, MailOpened, MailPage as MailPageData, MailRow } from '../../../api/types'
import { dateTime } from '../../../format'
import { sanitizeMailBody } from '../../../mailHtml'
import { MailSettings } from './MailSettings'
import { ARCHIVE_KEY, filterMailRows, mergeMailRows } from './mailUtils'

const SYSTEM = [
  { id: 1, name: 'Inbox' }, { id: 2, name: 'Sent' }, { id: 4, name: 'Corp' },
  { id: 8, name: 'Alliance' }, { id: 16, name: 'Mailing lists' },
]
// Archive auto-refresh when Mail is opened and the archive is older than this.
const ARCHIVE_STALE_MS = 5 * 60_000

interface Selection { characterId: number | null; labelId: number | null }

function folderTitle(sel: Selection, folders: MailFolders | undefined): string {
  const label = SYSTEM.find((s) => s.id === sel.labelId)?.name
    ?? folders?.characters.flatMap((c) => c.labels).find((l) => l.label_id === sel.labelId)?.name
    ?? 'All mail'
  const who = sel.characterId === null
    ? 'all characters'
    : folders?.characters.find((c) => c.character_id === sel.characterId)?.character_name ?? `#${sel.characterId}`
  return `${label} · ${who}`
}

function MailListRow({ mail, active, showWho, onOpen }: {
  mail: MailRow
  active: boolean
  showWho: boolean
  onOpen: () => void
}) {
  return (
    <UnstyledButton
      onClick={onOpen}
      w="100%"
      p="xs"
      aria-label={`Open mail ${mail.subject || '(no subject)'}`}
      aria-current={active ? 'true' : undefined}
      style={{
        borderBottom: '1px solid var(--mantine-color-default-border)',
        background: active ? 'var(--mantine-color-default-hover)' : undefined,
      }}
    >
      <Group justify="space-between" wrap="nowrap" gap="xs">
        <Text size="sm" fw={mail.is_read ? 400 : 700} truncate>{mail.from_name ?? (mail.from_id ? `#${mail.from_id}` : '–')}</Text>
        <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>{dateTime(mail.timestamp)}</Text>
      </Group>
      <Text size="sm" fw={mail.is_read ? 400 : 700} truncate>{mail.subject || '(no subject)'}</Text>
      {showWho && (
        <Group gap={4} mt={2}>
          {mail.received_by.map((r) => (
            <Badge key={r.character_id} size="xs" variant="light" color={r.is_read ? 'gray' : 'accent'}>
              {r.character_name}{r.archived ? ' · archive' : ''}
            </Badge>
          ))}
        </Group>
      )}
    </UnstyledButton>
  )
}

function Reader({ mail }: { mail: MailOpened }) {
  // The ONLY place a mail body becomes markup, and only after sanitizing.
  const html = useMemo(() => sanitizeMailBody(mail.body), [mail.body])
  return (
    <Stack gap="xs">
      <Title order={3}>{mail.subject || '(no subject)'}</Title>
      <Text size="sm">
        <Text span fw={600}>From </Text>{mail.from_name ?? (mail.from_id ? `#${mail.from_id}` : '–')}
      </Text>
      <Text size="sm">
        <Text span fw={600}>To </Text>
        {mail.recipients.map((r) => r.name ?? `#${r.recipient_id}`).join(', ') || '–'}
      </Text>
      <Text size="xs" c="dimmed">
        {dateTime(mail.timestamp)} · received by {mail.received_by.map((r) => r.character_name).join(', ')}
        {mail.archived ? ' · from the archive' : ''}
      </Text>
      <Divider my="xs" />
      <Box
        data-testid="mail-body"
        style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </Stack>
  )
}

export default function MailPage() {
  const queryClient = useQueryClient()
  const [sel, setSel] = useState<Selection>({ characterId: null, labelId: 1 })
  const [opened, setOpened] = useState<{ characterId: number; mailId: number } | null>(null)
  const [filter, setFilter] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)

  const folders = useQuery({ queryKey: ['char-mail', 'folders'], queryFn: charMailApi.folders })
  const list = useInfiniteQuery({
    queryKey: ['char-mail', 'mails', sel.characterId, sel.labelId],
    initialPageParam: null as Record<string, number> | null,
    queryFn: ({ pageParam }) =>
      charMailApi.mails({ labelId: sel.labelId, characterId: sel.characterId, cursors: pageParam }),
    getNextPageParam: (last: MailPageData) => (Object.keys(last.next_cursors).length > 0 ? last.next_cursors : undefined),
  })
  const search = useQuery({
    queryKey: ['char-mail', 'search', searchTerm, sel.characterId],
    queryFn: () => charMailApi.search(searchTerm, sel.characterId),
    enabled: searchTerm.length >= 2,
  })
  const mail = useQuery({
    queryKey: ['char-mail', 'mail', opened?.characterId, opened?.mailId],
    queryFn: () => charMailApi.open((opened as { characterId: number }).characterId, (opened as { mailId: number }).mailId),
    enabled: opened !== null,
  })
  const archive = useQuery({ queryKey: ARCHIVE_KEY, queryFn: charMailApi.archive })

  // Refresh the archives once when Mail opens with a stale one (debounced by
  // the ref: never while one is already in flight, never twice per visit).
  const didAutoRefresh = useRef(false)
  const refreshArchives = useMutation({
    mutationFn: () => charMailApi.refreshArchive(),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['char-mail'] }),
  })
  useEffect(() => {
    if (didAutoRefresh.current || !archive.data) return
    const stale = archive.data.characters.some((c) =>
      c.archive_enabled && c.shared && !c.reauth_needed
      && (!c.last_refresh_at || Date.now() - new Date(c.last_refresh_at).getTime() > ARCHIVE_STALE_MS),
    )
    if (stale) {
      didAutoRefresh.current = true
      refreshArchives.mutate()
    }
  }, [archive.data, refreshArchives])

  const anyArchived = archive.data?.characters.some((c) => c.archive_enabled && c.shared) ?? false
  const loaded = useMemo(() => mergeMailRows((list.data?.pages ?? []).flatMap((p) => p.mails)), [list.data])
  const inSearch = searchTerm.length >= 2 && search.data !== undefined
  const shown = inSearch ? mergeMailRows(search.data?.mails ?? []) : filterMailRows(loaded, filter)
  const statuses = (list.data?.pages[0]?.characters ?? []).filter((c) => c.state !== 'ok')
  const multi = (folders.data?.characters.length ?? 0) > 1 && sel.characterId === null

  const refreshAll = useMutation({
    mutationFn: async () => { if (anyArchived) await charMailApi.refreshArchive() },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['char-mail'] }),
  })

  const select = (next: Selection) => { setSel(next); setOpened(null); setSearchTerm(''); setFilter('') }

  return (
    <Container size="xl" py="lg">
      <Group justify="space-between" align="flex-start" mb="sm">
        <div>
          <Title order={1}>Mail</Title>
          <Text size="xs" c="dimmed">Read live from ESI. Nothing is stored unless you archive a character in Mail settings.</Text>
        </div>
        <Group gap="xs">
          <Button size="xs" variant="default" loading={refreshAll.isPending} onClick={() => refreshAll.mutate()}>
            Refresh
          </Button>
          <Button size="xs" variant="default" leftSection={<IconSettings size={14} />} onClick={() => setSettingsOpen(true)}>
            Mail settings
          </Button>
          <Button component={Link} to="/character-management" variant="subtle" leftSection={<IconArrowLeft size={14} />}>
            Back
          </Button>
        </Group>
      </Group>

      <Grid gap="md">
        <Grid.Col span={{ base: 12, md: 3 }}>
          <Stack gap={0} aria-label="Folders">
            {folders.isLoading && <Loader size="sm" color="accent" />}
            {SYSTEM.map((s) => (
              <NavLink
                component="button"
                key={s.id}
                label={s.name}
                active={sel.characterId === null && sel.labelId === s.id}
                rightSection={(folders.data?.unread[String(s.id)] ?? 0) > 0
                  ? <Badge size="sm" color="accent">{folders.data?.unread[String(s.id)]}</Badge> : null}
                onClick={() => select({ characterId: null, labelId: s.id })}
              />
            ))}
            <NavLink
                component="button"
              label="All mail"
              active={sel.characterId === null && sel.labelId === null}
              onClick={() => select({ characterId: null, labelId: null })}
            />
            <Divider my="xs" />
            {(folders.data?.characters ?? []).map((c) => (
              <NavLink
                component="button"
                key={c.character_id}
                label={c.character_name}
                description={c.state === 'reauth_needed' ? 're-auth needed' : c.state === 'error' ? c.detail : undefined}
                rightSection={c.total_unread > 0 ? <Badge size="sm" color="accent">{c.total_unread}</Badge> : null}
                defaultOpened={false}
              >
                {c.labels.map((l) => (
                  <NavLink
                component="button"
                    key={l.label_id}
                    label={l.name}
                    active={sel.characterId === c.character_id && sel.labelId === l.label_id}
                    rightSection={l.unread_count > 0 ? <Badge size="xs" variant="light">{l.unread_count}</Badge> : null}
                    onClick={() => select({ characterId: c.character_id, labelId: l.label_id })}
                  />
                ))}
              </NavLink>
            ))}
            {!folders.isLoading && (folders.data?.characters.length ?? 0) === 0 && (
              <Text size="sm" c="dimmed" p="xs">
                No character shares Mail yet. Tick it on the{' '}
                <Text component={Link} to="/character-management/characters" span c="accent" td="underline">Characters page</Text>.
              </Text>
            )}
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 4 }}>
          <Stack gap="xs">
            <Text fw={600}>{folderTitle(sel, folders.data)}</Text>
            <Group gap="xs" wrap="nowrap">
              <TextInput
                placeholder="Filter loaded mail"
                value={filter}
                onChange={(e) => { setFilter(e.currentTarget.value); setSearchTerm('') }}
                style={{ flex: 1 }}
                aria-label="Filter loaded mail"
              />
              <Tooltip
                multiline w={260} disabled={anyArchived}
                label="Full-text search needs the archive: live mail is never indexed. Switch it on in Mail settings."
              >
                <Button
                  size="xs" variant="default" disabled={!anyArchived || filter.trim().length < 2}
                  onClick={() => setSearchTerm(filter.trim())}
                >
                  Search archive
                </Button>
              </Tooltip>
            </Group>
            {inSearch && (
              <Alert color="info" variant="light" p="xs">
                Archive search results{search.data && search.data.unsearchable.length > 0
                  ? ` (not searchable, live only: ${search.data.unsearchable.map((c) => c.character_name).join(', ')})` : ''}.{' '}
                <Text span c="accent" td="underline" style={{ cursor: 'pointer' }} onClick={() => setSearchTerm('')}>Clear</Text>
              </Alert>
            )}
            {statuses.map((c) => (
              <Text key={c.character_id} size="xs" c="warn">
                {c.character_name}: {c.state === 'reauth_needed' ? 'needs a re-authorize with the mail scope' : c.detail}
              </Text>
            ))}
            <ScrollArea.Autosize mah={620}>
              {list.isLoading || (searchTerm.length >= 2 && search.isLoading)
                ? <Loader size="sm" color="accent" />
                : shown.length === 0
                  ? <Text size="sm" c="dimmed" p="xs">{filter || inSearch ? 'No matching mail.' : 'No mail here.'}</Text>
                  : shown.map((m) => (
                    <MailListRow
                      key={m.mail_id}
                      mail={m}
                      showWho={multi}
                      active={opened?.mailId === m.mail_id}
                      onOpen={() => setOpened({ characterId: m.received_by[0].character_id, mailId: m.mail_id })}
                    />
                  ))}
              {!inSearch && list.hasNextPage && (
                <Button fullWidth variant="subtle" size="xs" mt="xs" loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>
                  Load more
                </Button>
              )}
            </ScrollArea.Autosize>
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 5 }}>
          {opened === null ? <Text c="dimmed">Select a mail to read it.</Text>
            : mail.isLoading ? <Loader size="sm" color="accent" />
              : mail.error || !mail.data ? <Text c="danger">{(mail.error as Error | null)?.message ?? 'Could not load this mail.'}</Text>
                : <Reader mail={mail.data} />}
        </Grid.Col>
      </Grid>

      <Drawer opened={settingsOpen} onClose={() => setSettingsOpen(false)} position="right" size="md" title="Mail settings">
        <MailSettings />
      </Drawer>
    </Container>
  )
}
