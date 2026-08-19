import { useQuery } from '@tanstack/react-query'
import { Stack, Title, Table, Badge, Text } from '@mantine/core'

import { doctrineApi } from '../../api/client'
import { qty, isk } from '../../format'

const SOURCE_COLOR: Record<string, string> = { Build: 'accent', 'C-J': 'warn', Jita: 'dimmed' }

export default function ShoppingList() {
  const { data, isLoading } = useQuery({
    queryKey: ['doctrine', 'shopping-list'], queryFn: () => doctrineApi.shoppingList(),
  })
  const rows = data?.rows ?? []

  return (
    <Stack>
      <Title order={4}>Shopping List</Title>
      <Text size="sm" c="dimmed">
        Everything currently short across every doctrine (same combined shortfall as the Stockpile tab), with
        whichever of Build / buy at C-J / buy at Jita (landed, including import cost) is cheapest right now.
      </Text>

      {!isLoading && rows.length === 0 && <Text c="dimmed">Nothing short right now.</Text>}

      {rows.length > 0 && (
        <Table striped highlightOnHover fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Type</Table.Th>
              <Table.Th>Shortfall</Table.Th>
              <Table.Th>Build</Table.Th>
              <Table.Th>C-J</Table.Th>
              <Table.Th>Jita (landed)</Table.Th>
              <Table.Th>Recommended</Table.Th>
              <Table.Th>Total Cost</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r) => (
              <Table.Tr key={r.type_id}>
                <Table.Td>{r.type_name}</Table.Td>
                <Table.Td>{qty(r.shortfall)}</Table.Td>
                <Table.Td>{r.build_cost != null ? isk(r.build_cost) : '–'}</Table.Td>
                <Table.Td>{r.cj_price != null ? isk(r.cj_price) : '–'}</Table.Td>
                <Table.Td>{r.jita_landed_price != null ? isk(r.jita_landed_price) : '–'}</Table.Td>
                <Table.Td>
                  {r.recommended_source && (
                    <Badge size="sm" color={SOURCE_COLOR[r.recommended_source] ?? 'dimmed'} variant="light">
                      {r.recommended_source}
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>{r.total_cost != null ? isk(r.total_cost) : '–'}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  )
}
