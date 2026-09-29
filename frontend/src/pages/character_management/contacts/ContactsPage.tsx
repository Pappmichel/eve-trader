import { useState } from 'react'
import {
  Badge, Button, Container, Group, Loader, Modal, Select, Stack, Table, Tabs, Text, TextInput, Title,
} from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { charContactsApi } from '../../../api/client'
import type { CalendarEvent } from '../../../api/types'
import { FieldState } from '../../../components/FieldState'
import { dateTime } from '../../../format'

const KEY = ['char-contacts']
const TYPE_LABEL: Record<string, string> = {
  character: 'Character', corporation: 'Corporation', alliance: 'Alliance', faction: 'Faction', other: 'Other',
}

function ContactsTab({ characterId }: { characterId: number }) {
  const [filter, setFilter] = useState('')
  const { data, isLoading } = useQuery({
    queryKey: [...KEY, 'contacts', characterId], queryFn: () => charContactsApi.contacts(characterId), retry: false,
  })
  if (isLoading || !data) return <Loader color="accent" />
  return (
    <FieldState field={data}>
      {(v) => {
        const needle = filter.trim().toLowerCase()
        const rows = v.contacts.filter((c) => !needle || c.name.toLowerCase().includes(needle)
          || c.labels.some((l) => l.toLowerCase().includes(needle)))
        return (
          <Stack gap="sm">
            <TextInput placeholder="Filter by name or label" value={filter}
              onChange={(e) => setFilter(e.currentTarget.value)} maw={320} />
            {rows.length === 0 ? <Text c="dimmed">No contacts{needle ? ' match' : ''}.</Text> : (
              <Table.ScrollContainer minWidth={500}>
                <Table withTableBorder striped>
                  <Table.Thead>
                    <Table.Tr><Table.Th>Name</Table.Th><Table.Th>Type</Table.Th><Table.Th ta="right">Standing</Table.Th><Table.Th>Labels</Table.Th></Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {rows.map((c) => (
                      <Table.Tr key={c.contact_id}>
                        <Table.Td>
                          {c.name}
                          {c.is_blocked && <Badge ml="xs" size="xs" color="danger">blocked</Badge>}
                          {c.is_watched && <Badge ml="xs" size="xs" color="info">watched</Badge>}
                        </Table.Td>
                        <Table.Td>{TYPE_LABEL[c.contact_type] ?? c.contact_type}</Table.Td>
                        <Table.Td ta="right" c={c.standing < 0 ? 'danger' : undefined}>{c.standing.toFixed(1)}</Table.Td>
                        <Table.Td>{c.labels.join(', ')}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
            <Text size="xs" c="dimmed">{v.contacts.length} contacts · read-only, changed in the game.</Text>
          </Stack>
        )
      }}
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
  return (
    <>
      <FieldState field={data}>
        {(events) => events.length === 0 ? <Text c="dimmed">No upcoming events.</Text> : (
          <Table.ScrollContainer minWidth={400}>
            <Table withTableBorder striped highlightOnHover>
              <Table.Thead><Table.Tr><Table.Th>When</Table.Th><Table.Th>Event</Table.Th><Table.Th>Response</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>
                {events.map((e) => (
                  <Table.Tr key={e.event_id}>
                    <Table.Td>{dateTime(e.event_date)}</Table.Td>
                    <Table.Td><Button variant="subtle" size="compact-sm" onClick={() => setOpened(e)}>{e.title}</Button></Table.Td>
                    <Table.Td>{e.response ?? '–'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
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
