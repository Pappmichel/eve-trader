import { useState } from 'react'
import { Modal, Stack, Text, TextInput, Button, Group, Table, Loader } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import { adminApi } from '../../api/client'
import { useAction } from '../../hooks/useAction'
import type { AdminTenant } from '../../api/types'

/** Confirmed, irreversible delete of an orphaned tenant (no registered user).
 *  Shows the row count per table first; the button stays disabled until the
 *  tenant's exact name is typed. */
export function PurgeTenantDialog({ tenant, onClose }: { tenant: AdminTenant; onClose: () => void }) {
  const [confirmName, setConfirmName] = useState('')
  const { data: preview, isLoading, isError, error } = useQuery({
    queryKey: ['admin', 'tenant-purge-preview', tenant.tenant_id],
    queryFn: () => adminApi.purgeTenantPreview(tenant.tenant_id),
    retry: false,
    gcTime: 0,
  })
  const purge = useAction(
    'Delete tenant',
    async () => {
      const r = await adminApi.purgeTenant(tenant.tenant_id, confirmName)
      onClose()
      return `Deleted ${r.rows_deleted} rows in ${r.tables.length} tables; ${r.tokens_deleted} stored tokens removed.`
    },
    [['admin', 'tenants'], ['admin', 'users']],
  )

  return (
    <Modal opened onClose={onClose} title={`Delete tenant "${tenant.name}" for good`} size="lg">
      <Stack gap="sm">
        {isLoading && <Loader size="sm" />}
        {isError && <Text c="danger" size="sm">{String((error as Error)?.message ?? error)}</Text>}
        {preview && (
          <>
            <Text size="sm">
              This deletes {preview.total_rows} rows in {preview.tables.length} tables. It cannot be undone.
              Rolling backups keep the data until they rotate out.
            </Text>
            <Table withTableBorder fz="xs">
              <Table.Tbody>
                {preview.tables.map((t) => (
                  <Table.Tr key={t.table}><Table.Td>{t.table}</Table.Td><Table.Td ta="right">{t.rows}</Table.Td></Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            <TextInput
              label="Type the tenant name to confirm"
              placeholder={tenant.name}
              value={confirmName}
              onChange={(e) => setConfirmName(e.currentTarget.value)}
            />
          </>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button
            color="danger"
            disabled={!preview || confirmName !== tenant.name}
            loading={purge.isPending}
            onClick={() => purge.mutate()}
          >
            Delete for good
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
