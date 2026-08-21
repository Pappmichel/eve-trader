import { useMemo, useState } from 'react'
import {
  Container, Title, Text, Group, Stack, Button, TextInput, Checkbox, ActionIcon, Divider, Badge,
} from '@mantine/core'
import { IconArrowLeft, IconTrash } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'

import { adminApi, productionApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { dateTime } from '../../format'
import type { AdminTenant, AdminUser } from '../../api/types'
import { DataTable } from '../../components/DataTable'

// GitHub issue #34: the SDE cache is global/shared across every tenant, so
// refreshing it is a cross-tenant-impacting action - moved here from
// Production's own sidebar, which only ever exposed it per-tenant. The
// read-only freshness/counts queries stay on productionApi (still real
// per-request reads, just happen to reflect global data), only the mutating
// refresh action itself lives under adminApi.
function SdeDataSection() {
  const { data: sdeCounts } = useQuery({ queryKey: ['production', 'sde', 'counts'], queryFn: productionApi.sdeCounts })
  const { data: sdeFreshness } = useQuery({
    queryKey: ['production', 'sde-freshness'], queryFn: productionApi.sdeFreshness,
    staleTime: Infinity, refetchOnWindowFocus: false, retry: false,
  })
  const refreshSde = useAction('Refresh SDE', adminApi.refreshSde,
    [['production', 'sde', 'counts'], ['production', 'sde-freshness']])

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
      <Button size="xs" variant="default" onClick={() => refreshSde.mutate()} loading={refreshSde.isPending}>
        Refresh SDE
      </Button>
    </div>
  )
}

// Mirrors access_gate.ALL_TOOL_KEYS (eve_trader/access_gate.py) - kept in
// sync by hand, same as every other small fixed-vocabulary list already
// hardcoded on the frontend elsewhere in this app.
const ALL_TOOL_KEYS = ['trading', 'production', 'doctrine', 'portfolio', 'admin']

// Read-only - tenants are always created implicitly as part of "Add User"
// below (one dedicated tenant per character, enforced at the DB level, see
// docs/admin_schema.sql). Kept visible here mainly to spot orphaned tenants
// left behind by a removed user (do_remove_user intentionally leaves the
// tenant and its data in place).
function TenantSection() {
  const { data: tenants, isLoading } = useQuery({ queryKey: ['admin', 'tenants'], queryFn: adminApi.tenants })

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
      />
    </div>
  )
}

function UserToolCheckboxes({ user }: { user: AdminUser }) {
  const [toolKeys, setToolKeys] = useState<string[]>(user.tool_keys)
  const dirty = toolKeys.slice().sort().join(',') !== user.tool_keys.slice().sort().join(',')
  const saveTools = useAction('Save Tools', () => adminApi.setToolGrants(user.character_id, toolKeys),
    [['admin', 'users']])

  return (
    <Group gap="sm" wrap="wrap">
      <Checkbox.Group value={toolKeys} onChange={setToolKeys}>
        <Group gap="xs">
          {ALL_TOOL_KEYS.map((key) => (
            <Checkbox key={key} value={key} label={key} size="xs" />
          ))}
        </Group>
      </Checkbox.Group>
      {dirty && (
        <Button size="compact-xs" onClick={() => saveTools.mutate()} loading={saveTools.isPending}>
          Save
        </Button>
      )}
    </Group>
  )
}

function UsersSection() {
  const { data: users, isLoading } = useQuery({ queryKey: ['admin', 'users'], queryFn: adminApi.users })
  const [characterName, setCharacterName] = useState('')
  const addUser = useAction('Add User', () => adminApi.addUser(characterName),
    [['admin', 'users'], ['admin', 'tenants']])
  const removeUser = useAction('Remove User', adminApi.removeUser, [['admin', 'users']])

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
          onClick={() => removeUser.mutate(i.row.original.character_id)} loading={removeUser.isPending}>
          <IconTrash size={14} />
        </ActionIcon>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [removeUser])

  return (
    <div>
      <Title order={4} mb="xs">Users</Title>
      {!isLoading && (users ?? []).length === 0 && <Text c="dimmed" size="sm" mb="sm">No users registered yet.</Text>}
      {(isLoading || (users ?? []).length > 0) && (
        <DataTable
          data={users ?? []}
          columns={columns}
          tableId="admin-users"
          exportFilename="users"
          getRowId={(u) => String(u.character_id)}
          isLoading={isLoading}
        />
      )}
      <Group mt="sm">
        <TextInput placeholder="Character name" value={characterName}
          onChange={(e) => setCharacterName(e.currentTarget.value)} w={220} />
        <Button size="xs" disabled={!characterName.trim()} loading={addUser.isPending}
          onClick={() => { addUser.mutate(); setCharacterName('') }}>
          Add User
        </Button>
      </Group>
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
        <TenantSection />
        <Divider />
        <UsersSection />
      </Stack>
    </Container>
  )
}
