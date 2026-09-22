import { useMemo, useState } from 'react'
import {
  Container, Title, Text, Group, Stack, Button, TextInput, Checkbox, ActionIcon, Divider, Badge, Tooltip,
} from '@mantine/core'
import { IconArrowLeft, IconTrash } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { modals } from '@mantine/modals'
import type { ColumnDef } from '@tanstack/react-table'

import { adminApi, productionApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { ActionTierIcon, TIER_COPY } from '../../components/ActionTierIcon'
import { dateTime } from '../../format'
import type { AdminTenant, AdminUser, ErrorLogRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'

// GitHub issue #34: the SDE cache is global/shared across every tenant, so
// previewing/applying it is a cross-tenant-impacting action - moved here from
// Production's own sidebar, which only ever exposed it per-tenant. The
// read-only freshness/counts queries stay on productionApi (still real
// per-request reads, just happen to reflect global data), only the mutating
// preview/apply flow lives under adminApi.
function SdeDataSection() {
  const { data: sdeCounts } = useQuery({ queryKey: ['production', 'sde', 'counts'], queryFn: productionApi.sdeCounts })
  const { data: sdeFreshness } = useQuery({
    queryKey: ['production', 'sde-freshness'], queryFn: productionApi.sdeFreshness,
    staleTime: Infinity, refetchOnWindowFocus: false, retry: false,
  })

  return (
    <div>
      <Group justify="space-between" mb="xs" wrap="nowrap">
        <Title order={4}>SDE Data</Title>
        {sdeFreshness && (
          sdeFreshness.newer_sde_available ? (
            <Badge size="xs" color="warn" variant="light">Update available</Badge>
          ) : sdeFreshness.remote_check_succeeded ? (
            <Badge size="xs" color="accent" variant="light">Up to date</Badge>
          ) : (
            <Badge size="xs" color="gray" variant="light">Check failed</Badge>
          )
        )}
      </Group>
      <Text size="sm" c="dimmed" mb="xs">Blueprint materials/products/times from Fuzzwork - shared across every tenant.</Text>
      <Text size="sm" c="dimmed">Refreshed: {dateTime(sdeFreshness?.local_refreshed_at)}</Text>
      {sdeCounts && (
        <Group gap="md" mb="sm">
          {Object.entries(sdeCounts).map(([table, count]) => (
            <Text size="xs" c="dimmed" key={table}>{table}: {count.toLocaleString('en-US')}</Text>
          ))}
        </Group>
      )}
      <Tooltip label={`Loads blueprint materials/products/times live from Fuzzwork, then shows a diff to apply. Global across every tenant. ${TIER_COPY.live}`}
        multiline w={300}>
        <Button component={Link} to="/admin/sde-preview" size="xs" variant="default"
          rightSection={<ActionTierIcon tier="live" />}>
          Preview SDE Update
        </Button>
      </Tooltip>
    </div>
  )
}

// Same "cross-tenant-impacting cache, not a per-tenant Production button"
// reasoning as SdeDataSection above - see production/jita_price_cache.py's
// own docstring. Deliberately its own standalone action (not wired into
// Production's "Recompute Buy/Build List" or any other button) - normally
// refreshed automatically once an hour by the scheduler (see the
// "Background Scheduler" card on the Portfolio page for that job's own
// last-run/interval status); this button is only for forcing a fresh one
// on demand.
function JitaPriceCacheSection() {
  const refreshJitaPriceCache = useAction('Refresh Jita Price Cache', adminApi.refreshJitaPriceCache,
    [['portfolio', 'scheduler-status']],
    { tier: 'live', effect: 'Loads Jita prices live from ESI for every Production stock target, global across every tenant.' })

  return (
    <div>
      <Title order={4} mb="xs">Jita Price Cache</Title>
      <Text size="sm" c="dimmed" mb="xs">
        Shared, hourly-refreshed Jita price snapshot used by Production's Buy/Build list - shared across every
        tenant. See the Background Scheduler card on the Portfolio page for last-refreshed time.
      </Text>
      <Tooltip label={refreshJitaPriceCache.tooltip} disabled={!refreshJitaPriceCache.tooltip} multiline w={280}>
        <Button size="xs" variant="default" leftSection={refreshJitaPriceCache.tierIcon} onClick={() => refreshJitaPriceCache.mutate()}
          loading={refreshJitaPriceCache.isPending}>
          Refresh Now
        </Button>
      </Tooltip>
    </div>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// Moved from the Portfolio page (confirmed real misplacement 2026-09-21) -
// one pg_dump already covers every tenant's data in one shot (backup.py's
// own docstring), so creating a backup is a cross-tenant-impacting action,
// same "not a per-tenant button" reasoning as SdeDataSection/
// JitaPriceCacheSection above. This also let api/app.py drop a one-off
// gate exception (F-06) that used to require "admin" for just this one
// path while it lived under /api/portfolio/.
function BackupsSection() {
  const { data: backups } = useQuery({ queryKey: ['admin', 'backups'], queryFn: adminApi.backups })
  const createBackup = useAction('Backup Now', adminApi.createBackup, [['admin', 'backups']])

  return (
    <div>
      <Group justify="space-between" mb="xs" wrap="nowrap">
        <Title order={4}>Backups</Title>
        <Button size="xs" loading={createBackup.isPending} onClick={() => createBackup.mutate()}>
          Backup Now
        </Button>
      </Group>
      <Text size="sm" c="dimmed" mb="xs">
        Backs up the full database (every tenant) plus config.yaml. Kept under data/backups/; the oldest are
        pruned automatically once there are more than 14.
      </Text>
      {backups && backups.length === 0 && (
        <Text size="sm" c="dimmed">No backups yet.</Text>
      )}
      {backups && backups.length > 0 && (
        <Stack gap={4}>
          {backups.slice(0, 5).map((b) => (
            <Group key={b.name} justify="space-between">
              <Text size="xs" ff="monospace">{b.name}</Text>
              <Text size="xs" c="dimmed">{dateTime(b.created_at)} - {formatBytes(b.size_bytes)}</Text>
            </Group>
          ))}
          {backups.length > 5 && (
            <Text size="xs" c="dimmed">+ {backups.length - 5} more in data/backups/</Text>
          )}
        </Stack>
      )}
    </div>
  )
}

// Mirrors access_gate.ALL_TOOL_KEYS (eve_trader/access_gate.py) - kept in
// sync by hand, same as every other small fixed-vocabulary list already
// hardcoded on the frontend elsewhere in this app.
const ALL_TOOL_KEYS = ['trading', 'production', 'doctrine', 'refining', 'station_trading', 'sorting', 'portfolio', 'admin', 'characters']
const ESI_CONSUMING_TOOLS = ['trading', 'production', 'doctrine', 'station_trading', 'sorting']

function withAutoCharacters(keys: string[]): string[] {
  const hasEsi = keys.some((k) => ESI_CONSUMING_TOOLS.includes(k))
  if (hasEsi && !keys.includes('characters')) return [...keys, 'characters']
  return keys
}

// Read-only - tenants are always created implicitly as part of "Add User"
// below (one dedicated tenant per character, enforced at the DB level, see
// docs/admin_schema.sql). Kept visible here mainly to spot orphaned tenants
// left behind by a removed user (do_remove_user intentionally leaves the
// tenant and its data in place).
function TenantSection() {
  const { data: tenants, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['admin', 'tenants'], queryFn: adminApi.tenants })

  const columns = useMemo<ColumnDef<AdminTenant, any>[]>(() => [
    { header: 'Name', accessorKey: 'name', size: 220 },
    { header: 'Tenant ID', accessorKey: 'tenant_id', size: 300 },
    { header: 'Created', accessorKey: 'created_at', size: 180, cell: (i) => dateTime(i.getValue()) },
  ], [])

  return (
    <div>
      <Title order={4} mb="xs">Tenants</Title>
      <DataTable
        data={tenants ?? []}
        columns={columns}
        tableId="admin-tenants"
        exportFilename="tenants"
        getRowId={(t) => t.tenant_id}
        isLoading={isLoading}
        isError={isError}
        onRetry={() => refetch()}
        dataUpdatedAt={dataUpdatedAt}
      />
    </div>
  )
}

function UserToolCheckboxes({ user }: { user: AdminUser }) {
  const [toolKeys, setToolKeys] = useState<string[]>(user.tool_keys)
  const dirty = toolKeys.slice().sort().join(',') !== user.tool_keys.slice().sort().join(',')
  const saveTools = useAction('Save Tools', () => adminApi.setToolGrants(user.character_id, toolKeys),
    [['admin', 'users']])
  const hasEsi = toolKeys.some((k) => ESI_CONSUMING_TOOLS.includes(k))

  return (
    <Stack gap={4}>
      <Group gap="sm" wrap="wrap">
        <Checkbox.Group value={toolKeys} onChange={(next) => setToolKeys(withAutoCharacters(next))}>
          <Group gap="xs">
            {ALL_TOOL_KEYS.map((key) => (
              <Checkbox
                key={key}
                value={key}
                label={key}
                size="xs"
                disabled={key === 'characters' && hasEsi}
              />
            ))}
          </Group>
        </Checkbox.Group>
        {dirty && (
          <Button size="compact-xs" onClick={() => saveTools.mutate()} loading={saveTools.isPending}>
            Save
          </Button>
        )}
      </Group>
      {hasEsi && (
        <Text size="xs" c="dimmed">
          Characters is auto-ticked while an ESI-consuming tool is granted — the Characters
          page is how that tool&apos;s data-access is configured.
        </Text>
      )}
    </Stack>
  )
}

function UsersSection() {
  const { data: users, isLoading, isError, refetch, dataUpdatedAt } = useQuery({ queryKey: ['admin', 'users'], queryFn: adminApi.users })
  const [characterName, setCharacterName] = useState('')
  const addUser = useAction('Add User', () => adminApi.addUser(characterName),
    [['admin', 'users'], ['admin', 'tenants']],
    { tier: 'live', effect: 'Resolves the character name live via ESI and creates a new tenant.' })
  const removeUser = useAction('Remove User', adminApi.removeUser, [['admin', 'users']])
  // GitHub issue #59 (found in a full-codebase audit 2026-08-21): one shared
  // mutation instance reused across every user's Remove button - without
  // tracking which row is actually pending, clicking Remove for one user put
  // *every* user's button into the loading/disabled state, not just the one
  // being removed (same bug ProductionLayout.tsx's own removeCharacter/
  // pendingRoleKey comment already documents and fixes).
  const [pendingCharacterId, setPendingCharacterId] = useState<number | null>(null)

  const columns = useMemo<ColumnDef<AdminUser, any>[]>(() => [
    { header: 'Character', accessorKey: 'character_name', size: 180, cell: (i) => i.getValue() ?? '—' },
    { header: 'ID', accessorKey: 'character_id', size: 130 },
    { header: 'Tenant', accessorKey: 'tenant_name', size: 180 },
    {
      header: 'Tools', id: 'tools', size: 260, enableSorting: false,
      cell: (i) => <UserToolCheckboxes user={i.row.original} />,
    },
    {
      header: '', id: 'actions', size: 60, enableSorting: false,
      cell: (i) => (
        <ActionIcon size="sm" variant="subtle" color="danger"
          onClick={() => modals.openConfirmModal({
            title: 'Remove user',
            children: <Text size="sm">
              Remove {i.row.original.character_name ?? `#${i.row.original.character_id}`} from Admin?
              Their tenant and its data stay intact - they just lose access until re-added.
            </Text>,
            labels: { confirm: 'Remove', cancel: 'Cancel' },
            confirmProps: { color: 'danger' },
            onConfirm: () => { setPendingCharacterId(i.row.original.character_id); removeUser.mutate(i.row.original.character_id) },
          })}
          loading={removeUser.isPending && pendingCharacterId === i.row.original.character_id}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [removeUser, pendingCharacterId])

  return (
    <div>
      <Title order={4} mb="xs">Users</Title>
      {!isLoading && !isError && (users ?? []).length === 0 && <Text c="dimmed" size="sm" mb="sm">No users registered yet.</Text>}
      {(isLoading || isError || (users ?? []).length > 0) && (
        <DataTable
          data={users ?? []}
          columns={columns}
          tableId="admin-users"
          exportFilename="users"
          getRowId={(u) => String(u.character_id)}
          isLoading={isLoading}
          isError={isError}
          onRetry={() => refetch()}
          dataUpdatedAt={dataUpdatedAt}
        />
      )}
      <Group mt="sm">
        <TextInput placeholder="Character name" value={characterName}
          onChange={(e) => setCharacterName(e.currentTarget.value)} w={220} />
        <Tooltip label={addUser.tooltip} disabled={!addUser.tooltip} multiline w={280}>
          <Button size="xs" leftSection={addUser.tierIcon} disabled={!characterName.trim()} loading={addUser.isPending}
            onClick={() => { addUser.mutate(); setCharacterName('') }}>
            Add User
          </Button>
        </Tooltip>
      </Group>
    </div>
  )
}

// GitHub issue #88: self-hosted error tracking - ErrorBoundary.tsx and
// main.tsx's global error/unhandledrejection listeners report here, this
// is the only place that ever reads it back out. Cross-tenant (error_log
// is deliberately unscoped, see docs/observability_schema.sql), same
// pattern as TenantSection/UsersSection above.
function ErrorsSection() {
  const { data: errorRows, isLoading } = useQuery({ queryKey: ['admin', 'errors'], queryFn: () => adminApi.errors() })

  const columns = useMemo<ColumnDef<ErrorLogRow, any>[]>(() => [
    { header: 'When', accessorKey: 'created_at', size: 170, cell: (i) => dateTime(i.getValue()) },
    { header: 'Source', accessorKey: 'source', size: 170 },
    { header: 'Message', accessorKey: 'message', size: 360 },
    { header: 'Page', accessorKey: 'path', size: 200, cell: (i) => i.getValue() ?? '—' },
    { header: 'Tenant', accessorKey: 'tenant_id', size: 280, cell: (i) => i.getValue() ?? '—' },
  ], [])

  return (
    <div>
      <Title order={4} mb="xs">Recent Errors</Title>
      <Text size="sm" c="dimmed" mb="sm">
        The last 200 frontend errors reported across every tenant (a render crash, an uncaught exception, or an
        unhandled promise rejection) - best-effort, never blocks the page that hit the error.
      </Text>
      {!isLoading && (errorRows ?? []).length === 0 && <Text c="dimmed" size="sm">No errors reported yet.</Text>}
      {(isLoading || (errorRows ?? []).length > 0) && (
        <DataTable
          data={errorRows ?? []}
          columns={columns}
          tableId="admin-errors"
          exportFilename="errors"
          getRowId={(r) => String(r.id)}
          isLoading={isLoading}
          maxHeight={400}
        />
      )}
    </div>
  )
}

export default function AdminPage() {
  return (
    <Container size="md" py="xl">
      <Group justify="space-between" mb="lg">
        <div>
          <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>Cross-tenant superadmin</Text>
          <Title order={1}>Admin</Title>
        </div>
        <Button component={Link} to="/" variant="subtle" leftSection={<IconArrowLeft size={14} />}>Back</Button>
      </Group>

      <Stack gap="xl">
        <SdeDataSection />
        <Divider />
        <JitaPriceCacheSection />
        <Divider />
        <BackupsSection />
        <Divider />
        <TenantSection />
        <Divider />
        <UsersSection />
        <Divider />
        <ErrorsSection />
      </Stack>
    </Container>
  )
}
