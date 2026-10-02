import { useState } from 'react'
import {
  Badge, Button, Container, Drawer, Group, Image, Loader, Stack, Text, Title, Tooltip,
} from '@mantine/core'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'

import { charInfoApi } from '../../../api/client'
import type { CharInfoCharacter, CharInfoImplant, CharInfoStandingRow } from '../../../api/types'
import { Countdown } from '../../../components/Countdown'
import { DataTable } from '../../../components/DataTable'
import { FieldState } from '../../../components/FieldState'
import { dateTime, isk, qty } from '../../../format'
import { useCharacterSync } from '../../../hooks/useCharacterSync'
import { isStale, newestStamp, useSyncWhenStale } from '../../../hooks/useSyncWhenStale'

const OVERVIEW_KEY = ['char-info', 'overview']

const STANDING_COLUMNS: ColumnDef<CharInfoStandingRow, any>[] = [
  { header: 'Name', accessorKey: 'name', size: 260 },
  {
    header: 'Standing', accessorKey: 'standing', size: 100,
    cell: (i) => {
      const v = i.getValue() as number
      return <Text size="sm" c={v < 0 ? 'danger' : undefined}>{v.toFixed(2)}</Text>
    },
  },
]

type JournalRow = { id?: number | null; date: string; ref_type?: string | null; amount: number; row: number }
const JOURNAL_COLUMNS: ColumnDef<JournalRow, any>[] = [
  { header: 'Date', accessorKey: 'date', size: 170, cell: (i) => dateTime(i.getValue() as string) },
  {
    header: 'Type', id: 'ref_type', size: 200, meta: { filterable: true },
    accessorFn: (e) => (e.ref_type ?? 'unknown').replace(/_/g, ' '),
  },
  {
    header: 'Amount', accessorKey: 'amount', size: 150,
    cell: (i) => {
      const v = i.getValue() as number
      return <Text size="sm" c={v < 0 ? 'danger' : undefined}>{isk(v)}</Text>
    },
  },
]

const LOYALTY_COLUMNS: ColumnDef<{ corporation_id: number; corporation_name: string; loyalty_points: number }, any>[] = [
  { header: 'Corporation', accessorKey: 'corporation_name', size: 260 },
  { header: 'Loyalty points', accessorKey: 'loyalty_points', size: 140, cell: (i) => `${qty(i.getValue())} LP` },
]

type CorpHistoryRow = { corporation_id: number; corporation_name: string; start_date: string | null; row: number }
const CORP_HISTORY_COLUMNS: ColumnDef<CorpHistoryRow, any>[] = [
  { header: 'Corporation', accessorKey: 'corporation_name', size: 260 },
  { header: 'Since', accessorKey: 'start_date', size: 170, cell: (i) => dateTime(i.getValue() as string | null) },
]

function StandingsTable({ title, rows }: { title: string; rows: CharInfoStandingRow[] }) {
  if (rows.length === 0) return null
  return (
    <div>
      <Text size="sm" fw={600} mb={4}>{title}</Text>
      <DataTable
        data={rows} columns={STANDING_COLUMNS} maxHeight={320}
        getRowId={(r) => String(r.from_id)} exportFilename={`standings-${title.toLowerCase().replace(/\s+/g, '-')}`}
      />
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
            <DataTable
              data={j.entries.map((e, i) => ({ ...e, row: e.id ?? i }))} columns={JOURNAL_COLUMNS} maxHeight={320}
              getRowId={(e) => String(e.row)} exportFilename="wallet-journal"
            />
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
                <DataTable
                  data={rows} columns={LOYALTY_COLUMNS} maxHeight={320}
                  getRowId={(r) => String(r.corporation_id)} exportFilename="loyalty-points"
                />
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
            <DataTable
              data={(data.corporation_history ?? []).map((h, i) => ({ ...h, row: i }))} columns={CORP_HISTORY_COLUMNS} maxHeight={320}
              getRowId={(h) => `${h.corporation_id}-${h.row}`} exportFilename="corporation-history"
            />
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
  // Cells are two lines tall (name + corporation, system + station), so the overview rows are taller.
  const overviewColumns: ColumnDef<CharInfoCharacter, any>[] = [
    {
      header: 'Character', id: 'character', size: 220,
      accessorFn: (c) => c.character_name ?? `#${c.character_id}`,
      cell: (i) => {
        const c = i.row.original
        return (
          <>
            <Text size="sm" fw={600}>{c.character_name ?? `#${c.character_id}`}</Text>
            <Text size="xs" c="dimmed">{c.corporation_name ?? '–'}{c.alliance_name ? ` · ${c.alliance_name}` : ''}</Text>
          </>
        )
      },
    },
    {
      header: 'Security', accessorKey: 'security_status', size: 100,
      cell: (i) => (i.getValue() === null ? '–' : (i.getValue() as number).toFixed(2)),
    },
    {
      header: 'Wallet', id: 'wallet', size: 150,
      accessorFn: (c) => (c.wallet_balance.state === 'ok' ? c.wallet_balance.value : null),
      cell: (i) => <FieldState field={i.row.original.wallet_balance}>{(v) => isk(v)}</FieldState>,
    },
    {
      header: 'Status', id: 'status', size: 110,
      accessorFn: (c) => (c.online.state === 'ok' && c.online.value ? (c.online.value.online ? 'online' : 'offline') : null),
      cell: (i) => (
        <FieldState field={i.row.original.online}>
          {(o) => (
            <Badge size="sm" color={o.online ? 'accent' : 'gray'} variant="light">{o.online ? 'online' : 'offline'}</Badge>
          )}
        </FieldState>
      ),
    },
    {
      header: 'Location', id: 'location', size: 240,
      accessorFn: (c) => (c.location.state === 'ok' ? c.location.value?.solar_system_name ?? null : null),
      cell: (i) => (
        <FieldState field={i.row.original.location}>
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
      ),
    },
    {
      header: 'Ship', id: 'ship', size: 200,
      accessorFn: (c) => (c.ship.state === 'ok' ? c.ship.value?.ship_type_name ?? null : null),
      cell: (i) => (
        <FieldState field={i.row.original.ship}>
          {(sh) => (
            <div>
              <Text size="sm">{sh.ship_type_name ?? (sh.ship_type_id ? `Type ${sh.ship_type_id}` : '–')}</Text>
              {sh.ship_name && <Text size="xs" c="dimmed">{sh.ship_name}</Text>}
            </div>
          )}
        </FieldState>
      ),
    },
    {
      header: 'Jump fatigue', id: 'fatigue', size: 140, enableSorting: false,
      cell: (i) => {
        const c = i.row.original
        return c.fatigue ? (
          <FieldState field={c.fatigue}>
            {(f) => <Countdown until={f.jump_fatigue_expire_date} doneLabel="none" />}
          </FieldState>
        ) : null
      },
    },
    {
      header: '', id: 'details', size: 90, enableSorting: false, enableResizing: false,
      cell: (i) => (
        <Button size="compact-xs" variant="default" onClick={() => setSelected(i.row.original.character_id)}>Details</Button>
      ),
    },
  ]

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
        <DataTable
          data={characters} columns={overviewColumns} rowHeight={56} maxHeight={600}
          getRowId={(c) => String(c.character_id)} exportFilename="character-overview"
        />
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
