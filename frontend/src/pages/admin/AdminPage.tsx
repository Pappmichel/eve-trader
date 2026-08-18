import { useState } from 'react'
import {
  Container, Title, Text, Group, Stack, Button, Table, TextInput, Select, Checkbox, ActionIcon, Divider,
} from '@mantine/core'
import { IconArrowLeft, IconTrash } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { adminApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import { dateTime } from '../../format'
import type { AdminUser } from '../../api/types'

// Mirrors access_gate.ALL_TOOL_KEYS (eve_trader/access_gate.py) - kept in
// sync by hand, same as every other small fixed-vocabulary list already
// hardcoded on the frontend elsewhere in this app.
const ALL_TOOL_KEYS = ['trading', 'production', 'doctrine', 'portfolio', 'admin']

function TenantSection() {
  const { data: tenants } = useQuery({ queryKey: ['admin', 'tenants'], queryFn: adminApi.tenants })
  const [name, setName] = useState('')
  const createTenant = useAction('Create Tenant', () => adminApi.createTenant(name), [['admin', 'tenants']])

  return (
    <div>
      <Title order={4} mb="xs">Tenants</Title>
      <Table striped highlightOnHover fz="sm" mb="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Tenant ID</Table.Th>
            <Table.Th>Created</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(tenants ?? []).map((t) => (
            <Table.Tr key={t.tenant_id}>
              <Table.Td>{t.name}</Table.Td>
              <Table.Td>{t.tenant_id}</Table.Td>
              <Table.Td>{dateTime(t.created_at)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Group>
        <TextInput placeholder="Tenant name" value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <Button size="xs" disabled={!name.trim()} loading={createTenant.isPending}
          onClick={() => { createTenant.mutate(); setName('') }}>
          Create Tenant
        </Button>
      </Group>
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
  const { data: tenants } = useQuery({ queryKey: ['admin', 'tenants'], queryFn: adminApi.tenants })
  const [characterId, setCharacterId] = useState('')
  const [tenantId, setTenantId] = useState<string | null>(null)
  const addUser = useAction('Add User', () => adminApi.addUser(Number(characterId), tenantId!),
    [['admin', 'users']])
  const removeUser = useAction('Remove User', adminApi.removeUser, [['admin', 'users']])

  return (
    <div>
      <Title order={4} mb="xs">Users</Title>
      {!isLoading && (users ?? []).length === 0 && <Text c="dimmed" size="sm" mb="sm">No users registered yet.</Text>}
      {(users ?? []).length > 0 && (
        <Table striped highlightOnHover fz="sm" mb="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Character</Table.Th>
              <Table.Th>ID</Table.Th>
              <Table.Th>Tenant</Table.Th>
              <Table.Th>Tools</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(users ?? []).map((u) => (
              <Table.Tr key={u.character_id}>
                <Table.Td>{u.character_name ?? '—'}</Table.Td>
                <Table.Td>{u.character_id}</Table.Td>
                <Table.Td>{u.tenant_name}</Table.Td>
                <Table.Td><UserToolCheckboxes user={u} /></Table.Td>
                <Table.Td>
                  <ActionIcon size="sm" variant="subtle" color="danger"
                    onClick={() => removeUser.mutate(u.character_id)} loading={removeUser.isPending}>
                    <IconTrash size={14} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <Group>
        <TextInput placeholder="Character ID" value={characterId}
          onChange={(e) => setCharacterId(e.currentTarget.value)} w={140} />
        <Select placeholder="Tenant" data={(tenants ?? []).map((t) => ({ value: t.tenant_id, label: t.name }))}
          value={tenantId} onChange={setTenantId} w={220} />
        <Button size="xs" disabled={!characterId.trim() || !tenantId} loading={addUser.isPending}
          onClick={() => { addUser.mutate(); setCharacterId('') }}>
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
        <TenantSection />
        <Divider />
        <UsersSection />
      </Stack>
    </Container>
  )
}
