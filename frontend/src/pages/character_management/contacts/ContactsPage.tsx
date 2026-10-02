import { useState } from 'react'
import {
  Badge, Button, Container, Group, Loader, Modal, Select, Stack, Tabs, Text, Title,
} from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'

import { charContactsApi } from '../../../api/client'
import type { CalendarEvent } from '../../../api/types'
import { DataTable } from '../../../components/DataTable'
import { FieldState } from '../../../components/FieldState'
import { dateTime } from '../../../format'

const KEY = ['char-contacts']
const TYPE_LABEL: Record<string, string> = {
  character: 'Character', corporation: 'Corporation', alliance: 'Alliance', faction: 'Faction', other: 'Other',
}

type Contact = { contact_id: number; name: string; contact_type: string; standing: number; labels: string[]; is_blocked?: boolean; is_watched?: boolean }

const CONTACT_COLUMNS: ColumnDef<Contact, any>[] = [
  {
    header: 'Name', accessorKey: 'name', size: 260,
    cell: (i) => {
      const c = i.row.original
      return (
        <>
          {c.name}
          {c.is_blocked && <Badge ml="xs" size="xs" color="danger">blocked</Badge>}
          {c.is_watched && <Badge ml="xs" size="xs" color="info">watched</Badge>}
        </>
      )
    },
  },
  {
    header: 'Type', id: 'type', size: 120, meta: { filterable: true },
    accessorFn: (c) => TYPE_LABEL[c.contact_type] ?? c.contact_type,
  },
  {
    header: 'Standing', accessorKey: 'standing', size: 100,
    cell: (i) => {
      const standing = i.getValue() as number
      return <Text size="sm" ta="right" c={standing < 0 ? 'danger' : undefined}>{standing.toFixed(1)}</Text>
    },
  },
  { header: 'Labels', id: 'labels', size: 240, accessorFn: (c) => c.labels.join(', ') },
]

function ContactsTab({ characterId }: { characterId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: [...KEY, 'contacts', characterId], queryFn: () => charContactsApi.contacts(characterId), retry: false,
  })
  if (isLoading || !data) return <Loader color="accent" />
  return (
    <FieldState field={data}>
      {(v) => (
        <Stack gap="sm">
          {v.contacts.length === 0 ? <Text c="dimmed">No contacts.</Text> : (
            <DataTable
              data={v.contacts} columns={CONTACT_COLUMNS} maxHeight={560}
              getRowId={(c) => String(c.contact_id)} exportFilename="contacts"
            />
          )}
          <Text size="xs" c="dimmed">{v.contacts.length} contacts · read-only, changed in the game.</Text>
        </Stack>
      )}
    </FieldState>
  )
}

function EventModal({ characterId, event, onClose }: {
  characterId: number; event: CalendarEvent | null; onClose: () => void
}) {
  const { data, isLoading } = useQuery({
    queryKey: [...KEY, 'event', characterId, event?.event_id],
    queryFn: () => charContactsApi.event(characterId, event!.event_id),
    enabled: event !== null, retry: false,
  })
  return (
    <Modal opened={event !== null} onClose={onClose} title={event?.title ?? 'Event'} size="lg">
      {isLoading || !data ? <Loader color="accent" /> : (
        <FieldState field={data}>
          {(e) => (
            <Stack gap="xs">
              <Text size="sm">{dateTime(e.date)}{e.duration ? ` · ${e.duration} min` : ''}</Text>
              <Text size="xs" c="dimmed">Organiser: {e.owner_name ?? '–'}{e.response ? ` · your response: ${e.response}` : ''}</Text>
              <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{e.text || 'No description.'}</Text>
            </Stack>
          )}
        </FieldState>
      )}
    </Modal>
  )
}

function CalendarTab({ characterId }: { characterId: number }) {
  const [opened, setOpened] = useState<CalendarEvent | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: [...KEY, 'calendar', characterId], queryFn: () => charContactsApi.calendar(characterId), retry: false,
  })
  if (isLoading || !data) return <Loader color="accent" />
  const eventColumns: ColumnDef<CalendarEvent, any>[] = [
    { header: 'When', accessorKey: 'event_date', size: 180, cell: (i) => dateTime(i.getValue() as string) },
    {
      header: 'Event', accessorKey: 'title', size: 320,
      cell: (i) => <Button variant="subtle" size="compact-sm" onClick={() => setOpened(i.row.original)}>{i.row.original.title}</Button>,
    },
    { header: 'Response', id: 'response', size: 120, accessorFn: (e) => e.response ?? '–' },
  ]
  return (
    <>
      <FieldState field={data}>
        {(events) => events.length === 0 ? <Text c="dimmed">No upcoming events.</Text> : (
          <DataTable
            data={events} columns={eventColumns} maxHeight={480}
            getRowId={(e) => String(e.event_id)} exportFilename="calendar-events"
          />
        )}
      </FieldState>
      <EventModal characterId={characterId} event={opened} onClose={() => setOpened(null)} />
    </>
  )
}

export default function ContactsPage() {
  const [selected, setSelected] = useState<string | null>(null)
  const list = useQuery({ queryKey: [...KEY, 'characters'], queryFn: charContactsApi.characters })
  const characters = list.data?.characters ?? []
  const current = selected ?? (characters[0] ? String(characters[0].character_id) : null)
  const row = characters.find((c) => String(c.character_id) === current)

  return (
    <Container size="xl" py="xl">
      <Group justify="space-between" align="flex-start" mb="md">
        <div>
          <Title order={1}>Contacts &amp; Calendar</Title>
          <Text size="sm" c="dimmed">
            A character&apos;s contacts and upcoming events. Read live from ESI when you open a tab and never stored.
          </Text>
        </div>
        <Button component={Link} to="/character-management" variant="subtle" leftSection={<IconArrowLeft size={14} />}>
          Back
        </Button>
      </Group>

      <Text size="xs" c="dimmed" mb="md">
        What this page may show is decided on the{' '}
        <Text component={Link} to="/character-management/characters" span c="accent" td="underline">Characters page</Text>.
      </Text>

      {list.isLoading ? <Loader color="accent" /> : characters.length === 0 ? (
        <Text c="dimmed">No ESI characters registered yet. Add one on the Characters page.</Text>
      ) : (
        <Stack gap="md">
          <Select label="Character" value={current} onChange={setSelected} allowDeselect={false} maw={320}
            data={characters.map((c) => ({ value: String(c.character_id), label: c.character_name }))} />
          {row && (
            <Tabs defaultValue="contacts" keepMounted={false}>
              <Tabs.List mb="md">
                <Tabs.Tab value="contacts">Contacts</Tabs.Tab>
                <Tabs.Tab value="calendar">Calendar</Tabs.Tab>
              </Tabs.List>
              <Tabs.Panel value="contacts">
                {row.contacts.state === 'ok'
                  ? <ContactsTab characterId={row.character_id} />
                  : <FieldState field={row.contacts}>{() => null}</FieldState>}
              </Tabs.Panel>
              <Tabs.Panel value="calendar">
                {row.calendar.state === 'ok'
                  ? <CalendarTab characterId={row.character_id} />
                  : <FieldState field={row.calendar}>{() => null}</FieldState>}
              </Tabs.Panel>
            </Tabs>
          )}
        </Stack>
      )}
    </Container>
  )
}
