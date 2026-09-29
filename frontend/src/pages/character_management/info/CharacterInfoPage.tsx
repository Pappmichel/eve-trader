import { useState } from 'react'
import {
  Badge, Button, Container, Drawer, Group, Image, Loader, Stack, Table, Text, Title, Tooltip,
} from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { charInfoApi } from '../../../api/client'
import type { CharInfoCharacter, CharInfoImplant, CharInfoStandingRow } from '../../../api/types'
import { Countdown } from '../../../components/Countdown'
import { FieldState } from '../../../components/FieldState'
import { dateTime, isk, qty } from '../../../format'
import { useCharacterSync } from '../../../hooks/useCharacterSync'
import { isStale, newestStamp, useSyncWhenStale } from '../../../hooks/useSyncWhenStale'

const OVERVIEW_KEY = ['char-info', 'overview']

function StandingsTable({ title, rows }: { title: string; rows: CharInfoStandingRow[] }) {
  if (rows.length === 0) return null
  return (
    <div>
      <Text size="sm" fw={600} mb={4}>{title}</Text>
      <Table.ScrollContainer minWidth={0}>
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
      </Table.ScrollContainer>
    </div>
  )
}

// The journal is a live ESI read (never stored), so it is only fetched when asked for.
function WalletJournal({ characterId }: { characterId: number }) {
  const [wanted, setWanted] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['char-info', 'wallet-journal', characterId], queryFn: () => charInfoApi.walletJournal(characterId),
    enabled: wanted, retry: false,
  })
  if (!wanted) {
    return <Button size="compact-sm" variant="default" onClick={() => setWanted(true)}>Show wallet journal</Button>
  }
  if (isLoading || !data) return <Loader color="accent" size="sm" />
  return (
    <FieldState field={data}>
      {(j) => (
        <Stack gap="xs">
          <Text size="xs" c="dimmed">
            Last {j.window_days} days (all ESI keeps) · income {isk(j.income)} · expenses {isk(j.expense)}
            {j.truncated ? ` · showing the newest ${j.entries.length} of ${j.total_entries}` : ''}
          </Text>
          {j.entries.length === 0 ? <Text size="sm" c="dimmed">No journal entries.</Text> : (
            <div style={{ maxHeight: 320, overflowY: 'auto' }}>
              <Table.ScrollContainer minWidth={0}>
                <Table withTableBorder striped>
                  <Table.Tbody>
                    {j.entries.map((e, i) => (
                      <Table.Tr key={e.id ?? i}>
                        <Table.Td>{dateTime(e.date)}</Table.Td>
                        <Table.Td>{(e.ref_type ?? 'unknown').replace(/_/g, ' ')}</Table.Td>
                        <Table.Td ta="right" c={e.amount < 0 ? 'danger' : undefined}>{isk(e.amount)}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </div>
          )}
        </Stack>
      )}
    </FieldState>
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
                <Table.ScrollContainer minWidth={0}>
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
                </Table.ScrollContainer>
              )}
          </FieldState>
        )}
      </div>

      <div>
        <Title order={4} mb={4}>Wallet journal</Title>
        {data.wallet_balance.state === 'not_shared'
          ? <Text size="xs" c="dimmed">Not shared: tick Wallet for Character Info on the Characters page.</Text>
          : <WalletJournal characterId={data.character_id} />}
      </div>

      <div>
        <Title order={4} mb={4}>Implants</Title>
        {data.implants && (
          <FieldState field={data.implants}>
            {(rows) => <ImplantList implants={rows} empty="No implants plugged in." />}
          </FieldState>
        )}
      </div>

      <div>
        <Title order={4} mb={4}>Clones</Title>
        {data.clones && (
          <FieldState field={data.clones}>
            {(c) => (
              <Stack gap="xs">
                <Text size="sm">
                  Home: {c.home ? (c.home.location_name ?? `${c.home.location_type ?? 'location'} #${c.home.location_id}`) : 'not set'}
                </Text>
                <Text size="xs" c="dimmed">
                  Last jump {dateTime(c.last_clone_jump_date)} · last home change {dateTime(c.last_station_change_date)}
                </Text>
                <Text size="sm">
                  Next clone jump in{' '}
                  <Tooltip label="24 h after the last jump. Infomorph Synchronizing can shorten this by up to 4 h, so it may be ready sooner." multiline w={260}>
                    <span><Countdown until={c.clone_jump_available_at} doneLabel="available now" /></span>
                  </Tooltip>
                </Text>
                {c.jump_clones.length === 0
                  ? <Text size="sm" c="dimmed">No jump clones.</Text>
                  : c.jump_clones.map((jc) => (
                    <div key={jc.jump_clone_id}>
                      <Text size="sm" fw={600}>
                        {jc.name ? `${jc.name} – ` : ''}
                        {jc.location_name ?? `${jc.location_type ?? 'location'} #${jc.location_id ?? '?'}`}
                      </Text>
                      <ImplantList implants={jc.implants} empty="No implants." />
                    </div>
                  ))}
              </Stack>
            )}
          </FieldState>
        )}
      </div>

      <div>
        <Title order={4} mb={4}>Corporation history</Title>
        {(data.corporation_history ?? []).length === 0
          ? <Text size="sm" c="dimmed">Unavailable.</Text>
          : (
            <Table.ScrollContainer minWidth={0}>
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
            </Table.ScrollContainer>
          )}
      </div>
    </Stack>
  )
}

function ImplantList({ implants, empty }: { implants: CharInfoImplant[]; empty: string }) {
  if (implants.length === 0) return <Text size="xs" c="dimmed">{empty}</Text>
  return (
    <Stack gap={0}>
      {implants.map((i) => <Text size="xs" key={i.type_id}>{i.name}</Text>)}
    </Stack>
  )
}

function lastSynced(c: CharInfoCharacter): string | null {
  const stamps = ['wallet_balance', 'standings', 'loyalty', 'clones', 'implants']
    .map((k) => c.freshness[k]?.last_success_at)
    .filter((v): v is string => !!v)
    .sort()
  return stamps.length ? stamps[stamps.length - 1] : null
}

const SNAPSHOT_KINDS = ['wallet_balance', 'standings', 'loyalty', 'clones', 'implants']
const AUTO_SYNC_KEYS = [['char-info']]

export default function CharacterInfoPage() {
  const [selected, setSelected] = useState<number | null>(null)
  const overview = useQuery({ queryKey: OVERVIEW_KEY, queryFn: charInfoApi.overview })
  const characters = overview.data?.characters ?? []

  // Refresh = sync the snapshot kinds (wallet balance, standings, loyalty, clones, implants).
  // Location/ship/online are live reads and are refetched with the overview
  // itself.
  const refresh = useCharacterSync(
    'Character Info refresh',
    charInfoApi.sync,
    [['char-info']],
    'Syncs wallet balance, standings, loyalty points, clones and implants for every character shared with Character Info, and re-reads live location, ship and online status.',
  )

  const newest = characters.map(lastSynced).filter((v): v is string => !!v).sort().at(-1) ?? null

  // These snapshot kinds are no longer refreshed in the background (esi_data
  // `on_demand`), so opening the page with old data syncs them once.
  useSyncWhenStale({
    ready: overview.isSuccess,
    stale: isStale(characters.map((c) => newestStamp(SNAPSHOT_KINDS.map((k) => c.freshness[k]?.last_attempt_at)))),
    sync: charInfoApi.sync,
    invalidateKeys: AUTO_SYNC_KEYS,
  })

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
        . Location, ship, online status and jump fatigue are read live and never stored.
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
                <Table.Th>Jump fatigue</Table.Th>
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
                    {c.fatigue && (
                      <FieldState field={c.fatigue}>
                        {(f) => <Countdown until={f.jump_fatigue_expire_date} doneLabel="none" />}
                      </FieldState>
                    )}
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
