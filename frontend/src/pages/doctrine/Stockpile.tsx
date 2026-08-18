import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Table, Badge, Text, Divider } from '@mantine/core'

import { doctrineApi } from '../../api/client'
import { HintCard } from '../../components/HintCard'
import { qty } from '../../format'

const SEVERITY_COLOR: Record<string, string> = { critical: 'danger', tolerable: 'warn' }

export default function Stockpile() {
  const { data, isLoading } = useQuery({ queryKey: ['doctrine', 'stockpile'], queryFn: () => doctrineApi.stockpile() })

  const rows = data?.rows ?? []
  const aggregatedRows = data?.aggregated_rows ?? []
  const byDoctrine: Record<string, typeof rows> = {}
  for (const r of rows) {
    (byDoctrine[r.doctrine_id] ??= []).push(r)
  }

  return (
    <Stack>
      <Title order={4}>Stockpile</Title>

      {!isLoading && data && !data.assets_available && (
        <HintCard>
          No asset data available yet - add an asset-scanning character under "Asset-Scanning Characters" in the
          sidebar and run "Sync Assets" to populate stock levels here.
        </HintCard>
      )}

      {!isLoading && data?.assets_available && rows.length === 0 && (
        <Text c="dimmed">No stockpile targets configured (every fitting's stockpile target is 0).</Text>
      )}

      {aggregatedRows.length > 0 && (
        <div>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">
            Combined Shortfall (across every doctrine/fitting)
          </Title>
          <Table striped highlightOnHover fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Type</Table.Th>
                <Table.Th>Required</Table.Th>
                <Table.Th>Available</Table.Th>
                <Table.Th>Shortfall</Table.Th>
                <Table.Th>Used in</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {aggregatedRows.map((r) => (
                <Table.Tr key={r.type_id}>
                  <Table.Td>{r.type_name}</Table.Td>
                  <Table.Td>{qty(r.required_total)}</Table.Td>
                  <Table.Td>{qty(r.available)}</Table.Td>
                  <Table.Td>{r.shortfall > 0 ? qty(r.shortfall) : '–'}</Table.Td>
                  <Table.Td>{r.fitting_count} fitting{r.fitting_count === 1 ? '' : 's'}</Table.Td>
                  <Table.Td>
                    {r.severity && (
                      <Badge size="xs" color={SEVERITY_COLOR[r.severity] ?? 'dimmed'} variant="light">{r.severity}</Badge>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
      )}

      {rows.length > 0 && <Divider label="By doctrine / fitting" labelPosition="left" />}

      {Object.entries(byDoctrine).map(([doctrineId, doctrineRows]) => (
        <div key={doctrineId}>
          <Title order={6} c="dimmed" tt="uppercase" mb="xs">{doctrineRows[0]?.doctrine_name}</Title>
          <Table striped highlightOnHover fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Fitting</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>Section</Table.Th>
                <Table.Th>Required</Table.Th>
                <Table.Th>Available</Table.Th>
                <Table.Th>Shortfall</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {doctrineRows.map((r) => (
                <Table.Tr key={`${r.fitting_id}-${r.type_id}`}>
                  <Table.Td>{r.fitting_name}</Table.Td>
                  <Table.Td>{r.type_name}</Table.Td>
                  <Table.Td>{r.slot_section}</Table.Td>
                  <Table.Td>{qty(r.required_total)}</Table.Td>
                  <Table.Td>{qty(r.available)}</Table.Td>
                  <Table.Td>{r.shortfall > 0 ? qty(r.shortfall) : '–'}</Table.Td>
                  <Table.Td>
                    {r.severity && (
                      <Badge size="xs" color={SEVERITY_COLOR[r.severity] ?? 'dimmed'} variant="light">{r.severity}</Badge>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
      ))}
    </Stack>
  )
}
