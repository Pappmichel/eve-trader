import { useState, type ReactNode } from 'react'
import {
  Badge, Button, Container, Drawer, Group, Image, Loader, Stack, Table, Text, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { charInfoApi } from '../../../api/client'
import type {
  CharInfoCharacter, CharInfoField, CharInfoStandingRow,
} from '../../../api/types'
import { dateTime, isk, qty } from '../../../format'
import { useAction } from '../../../hooks/useAction'

const OVERVIEW_KEY = ['char-info', 'overview']

// One place that says what each non-ok state means, so the table and the
// detail drawer never disagree (docs/CHARACTER_MANAGEMENT_PLAN.md phase 1).
function FieldState<T>({ field, children }: { field: CharInfoField<T>; children: (value: T) => ReactNode }) {
  if (field.state === 'ok' && field.value !== null) return <>{children(field.value)}</>
  if (field.state === 'ok') return <Text size="xs" c="dimmed">–</Text>
  if (field.state === 'not_shared') {
    return (
      <Tooltip label="Not shared with Character Info. Tick it on the Characters page." multiline w={240}>
        <Text size="xs" c="dimmed">not shared</Text>
      </Tooltip>
    )
  }
  if (field.state === 'reauth_needed') {
    return (
      <Tooltip label="Shared, but no stored token carries the needed scope. Re-authorize this character on the Characters page." multiline w={260}>
        <Badge size="xs" color="warn" variant="light">re-auth needed</Badge>
      </Tooltip>
    )
  }
  if (field.state === 'not_synced') {
    return (
      <Tooltip label={field.detail ?? 'Not synced yet. Press Refresh.'} multiline w={240}>
        <Text size="xs" c="dimmed">not synced yet</Text>
      </Tooltip>
    )
  }
  return (
    <Tooltip label={field.detail ?? 'ESI request failed'} multiline w={260}>
      <Badge size="xs" color="danger" variant="light">error</Badge>
    </Tooltip>
  )
}

function StandingsTable({ title, rows }: { title: string; rows: CharInfoStandingRow[] }) {
  if (rows.length === 0) return null
  return (
    <div>
      <Text size="sm" fw={600} mb={4}>{title}</Text>
      <Table withTableBorder striped>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.from_id}>
              <Table.Td>{r.name}</Table.Td>
              <Table.Td ta="right" c={r.standing < 0 ? 'danger' : undefined}>{r.standing.toFixed(2)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </div>
  )
}

function CharacterDetail({ characterId }: { characterId: number }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['char-info', 'detail', characterId],
    queryFn: () => charInfoApi.detail(characterId),
  })
  if (isLoading) return <Loader color="accent" />
  if (error || !data) return <Text c="danger">Could not load this character.</Text>

  return (
    <Stack gap="lg">
      <Group wrap="nowrap" align="flex-start">
        <Image
          src={`https://images.evetech.net/characters/${data.character_id}/portrait?size=128`}
          w={96} h={96} radius="md" alt={`${data.character_name ?? data.character_id} portrait`}
        />
        <div>
          <Title order={3}>{data.character_name}</Title>
          <Text size="sm">{data.corporation_name ?? '–'}{data.alliance_name ? ` · ${data.alliance_name}` : ''}</Text>
          <Text size="xs" c="dimmed">
            Security {data.security_status === null ? '–' : data.security_status.toFixed(2)}
            {data.birthday ? ` · born ${dateTime(data.birthday)}` : ''}
          </Text>
        </div>
      </Group>

      <div>
        <Title order={4} mb={4}>Standings</Title>
        {data.standings && (
          <FieldState field={data.standings}>
            {(s) => (
              s.faction.length + s.npc_corp.length + s.agent.length === 0
                ? <Text size="sm" c="dimmed">No standings.</Text>
                : (
                  <Stack gap="sm">
                    <StandingsTable title="Factions" rows={s.faction} />
                    <StandingsTable title="NPC corporations" rows={s.npc_corp} />
                    <StandingsTable title="Agents" rows={s.agent} />
                  </Stack>
                )
            )}
          </FieldState>
        )}
      </div>

      <div>
        <Title order={4} mb={4}>Loyalty points</Title>
        {data.loyalty_points && (
          <FieldState field={data.loyalty_points}>
            {(rows) => rows.length === 0
              ? <Text size="sm" c="dimmed">No loyalty points.</Text>
              : (
                <Table withTableBorder striped>
                  <Table.Tbody>
                    {rows.map((r) => (
                      <Table.Tr key={r.corporation_id}>
                        <Table.Td>{r.corporation_name}</Table.Td>
                        <Table.Td ta="right">{qty(r.loyalty_points)} LP</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
          </FieldState>
        )}
      </div>

      <div>
        <Title order={4} mb={4}>Corporation history</Title>
        {(data.corporation_history ?? []).length === 0
          ? <Text size="sm" c="dimmed">Unavailable.</Text>
          : (
            <Table withTableBorder striped>
              <Table.Tbody>
                {(data.corporation_history ?? []).map((h, i) => (
                  <Table.Tr key={`${h.corporation_id}-${i}`}>
                    <Table.Td>{h.corporation_name}</Table.Td>
                    <Table.Td ta="right">{dateTime(h.start_date)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
      </div>
    </Stack>
  )
}

function lastSynced(c: CharInfoCharacter): string | null {
  const stamps = ['wallet_balance', 'standings', 'loyalty']
    .map((k) => c.freshness[k]?.last_success_at)
    .filter((v): v is string => !!v)
    .sort()
  return stamps.length ? stamps[stamps.length - 1] : null
}

export default function CharacterInfoPage() {
  const [selected, setSelected] = useState<number | null>(null)
  const overview = useQuery({ queryKey: OVERVIEW_KEY, queryFn: charInfoApi.overview })
  const characters = overview.data?.characters ?? []

  // Refresh = sync the snapshot kinds (wallet balance, standings, loyalty).
  // Location/ship/online are live reads and are refetched with the overview
  // itself. `in_flight` is neither success nor failure (plan R10).
  const refresh = useAction(
    'Character Info refresh',
    async () => {
      const result = await charInfoApi.sync()
      if (result.in_flight.length > 0) {
        notifications.show({
          title: 'Sync already running',
          message: `${result.in_flight.length} character(s) are already being synced by another run. Their data updates when it finishes.`,
          color: 'info',
        })
      }
      for (const f of result.failed) {
        notifications.show({
          title: `Sync failed for ${f.name ?? f.owner_id}`,
          message: f.error ?? 'Unknown error',
          color: 'danger',
        })
      }
      return { ok: result.ok, in_flight: result.in_flight.length, failed: result.failed.length }
    },
    [['char-info']],
    {
      tier: 'live',
      effect: 'Syncs wallet balance, standings and loyalty points for every character shared with Character Info, and re-reads live location, ship and online status.',
    },
  )

  const newest = characters.map(lastSynced).filter((v): v is string => !!v).sort().at(-1) ?? null

  return (
    <Container size="xl" py="xl">
      <Group justify="space-between" align="flex-start" mb="md">
        <div>
          <Title order={1}>Character Info</Title>
          <Text size="sm" c="dimmed">
            Where your characters are, what they fly, and what they hold.
            {newest ? ` Snapshot data last synced ${dateTime(newest)}.` : ' No snapshot data synced yet.'}
          </Text>
        </div>
        <Group gap="xs">
          <Tooltip label={refresh.tooltip} disabled={!refresh.tooltip} multiline w={280}>
            <Button
              size="xs" variant="default" leftSection={refresh.tierIcon}
              onClick={() => refresh.mutate()} loading={refresh.isPending}
            >
              Refresh
            </Button>
          </Tooltip>
          <Button component={Link} to="/character-management" variant="subtle" leftSection={<IconArrowLeft size={14} />}>
            Back
          </Button>
        </Group>
      </Group>

      <Text size="xs" c="dimmed" mb="md">
        What this page may show is decided on the{' '}
        <Text component={Link} to="/character-management/characters" span c="accent" td="underline">Characters page</Text>
        . Location, ship and online status are read live and never stored.
      </Text>

      {overview.isLoading ? <Loader color="accent" /> : characters.length === 0 ? (
        <Text c="dimmed">No ESI characters registered yet. Add one on the Characters page.</Text>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <Table striped highlightOnHover withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Character</Table.Th>
                <Table.Th>Security</Table.Th>
                <Table.Th>Wallet</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Location</Table.Th>
                <Table.Th>Ship</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {characters.map((c) => (
                <Table.Tr key={c.character_id}>
                  <Table.Td>
                    <Text size="sm" fw={600}>{c.character_name ?? `#${c.character_id}`}</Text>
                    <Text size="xs" c="dimmed">
                      {c.corporation_name ?? '–'}{c.alliance_name ? ` · ${c.alliance_name}` : ''}
                    </Text>
                  </Table.Td>
                  <Table.Td>{c.security_status === null ? '–' : c.security_status.toFixed(2)}</Table.Td>
                  <Table.Td><FieldState field={c.wallet_balance}>{(v) => isk(v)}</FieldState></Table.Td>
                  <Table.Td>
                    <FieldState field={c.online}>
                      {(o) => (
                        <Badge size="sm" color={o.online ? 'accent' : 'gray'} variant="light">
                          {o.online ? 'online' : 'offline'}
                        </Badge>
                      )}
                    </FieldState>
                  </Table.Td>
                  <Table.Td>
                    <FieldState field={c.location}>
                      {(l) => (
                        <div>
                          <Text size="sm">{l.solar_system_name ?? (l.solar_system_id ? `System ${l.solar_system_id}` : '–')}</Text>
                          {l.location_id && (
                            <Text size="xs" c="dimmed">
                              {l.location_name ?? `${l.location_kind === 'structure' ? 'Structure' : 'Station'} ${l.location_id}`}
                            </Text>
                          )}
                        </div>
                      )}
                    </FieldState>
                  </Table.Td>
                  <Table.Td>
                    <FieldState field={c.ship}>
                      {(s) => (
                        <div>
                          <Text size="sm">{s.ship_type_name ?? (s.ship_type_id ? `Type ${s.ship_type_id}` : '–')}</Text>
                          {s.ship_name && <Text size="xs" c="dimmed">{s.ship_name}</Text>}
                        </div>
                      )}
                    </FieldState>
                  </Table.Td>
                  <Table.Td>
                    <Button size="compact-xs" variant="default" onClick={() => setSelected(c.character_id)}>
                      Details
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
      )}

      <Drawer
        opened={selected !== null}
        onClose={() => setSelected(null)}
        position="right"
        size="lg"
        title="Character details"
      >
        {selected !== null && <CharacterDetail characterId={selected} />}
      </Drawer>
    </Container>
  )
}
