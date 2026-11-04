import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useDebouncedValue } from '@mantine/hooks'
import {
  Alert, Badge, Button, Card, Center, Group, Loader, ScrollArea, Select, Stack, Switch, Table, Text, TextInput, Title,
  Tooltip,
} from '@mantine/core'
import { IconPlus, IconSearch } from '@tabler/icons-react'

import { doctrineApi } from '../../api/client'
import type { EquivalentClass, EquivalentMember } from '../../api/types'
import { useAction } from '../../hooks/useAction'
import { isk } from '../../format'

const PAGE = 40
const INVALIDATE = [['doctrine', 'equivalents'], ['doctrine', 'status'], ['doctrine', 'contracts'],
  ['doctrine', 'stockpile'], ['doctrine', 'shopping-list']]

function PriceCell({ value, cheapest, note }: { value: number | null; cheapest: boolean; note?: string }) {
  if (value == null) {
    return <Table.Td ta="right"><Text size="xs" c="dimmed" fs={note ? 'italic' : undefined}>{note ?? '–'}</Text></Table.Td>
  }
  return (
    <Table.Td ta="right" style={{ fontVariantNumeric: 'tabular-nums' }}>
      {cheapest
        ? <Badge size="sm" color="accent" variant="light">{isk(value)}</Badge>
        : <Text size="sm">{isk(value)}</Text>}
    </Table.Td>
  )
}

function AddItem({ cls }: { cls: EquivalentClass }) {
  const anchor = cls.members.find((m) => !m.excluded) ?? cls.members[0]
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [debounced] = useDebouncedValue(query, 250)
  const { data } = useQuery({
    queryKey: ['doctrine', 'equivalents', 'candidates', anchor.type_id, debounced],
    queryFn: () => doctrineApi.equivalentCandidates(anchor.type_id, debounced.trim()),
    enabled: open && debounced.trim().length >= 2,
  })
  const add = useAction('Add to group', (typeId: number) => doctrineApi.addEquivalent(typeId, anchor.type_id),
    INVALIDATE, { tier: 'local', effect: 'Treats this item as interchangeable with the group and re-checks stored contracts.' })
  const inGroup = new Set(cls.members.map((m) => m.type_id))
  const options = (data ?? []).filter((c) => !inGroup.has(c.type_id))
    .map((c) => ({ value: String(c.type_id), label: c.type_name }))

  if (!open) {
    return (
      <Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={12} />} onClick={() => setOpen(true)}>
        Add item
      </Button>
    )
  }
  return (
    <Select size="xs" w={300} searchable autoFocus placeholder="Search this item group…" data={options}
      searchValue={query} onSearchChange={setQuery} nothingFoundMessage={debounced.trim().length >= 2 ? 'No match' : 'Type 2+ letters'}
      onChange={(v) => { if (v) { add.mutate(Number(v)); setOpen(false); setQuery('') } }}
      onBlur={() => { if (!query) setOpen(false) }} />
  )
}

function ClassCard({ cls }: { cls: EquivalentClass }) {
  const exclude = useAction('Update group', ({ typeId, excluded }: { typeId: number; excluded: boolean }) =>
    doctrineApi.setEquivalentExcluded(typeId, excluded), INVALIDATE, { tier: 'local' })
  const disable = useAction('Update group', (disabled: boolean) =>
    doctrineApi.setEquivalentGroupDisabled(cls.anchor_type_id, disabled), INVALIDATE, { tier: 'local' })
  const meta = (m: EquivalentMember) => [m.meta_group, m.manual ? 'added by you' : null].filter(Boolean).join(' · ')

  return (
    <Card withBorder padding={0} radius="sm" style={{ opacity: cls.disabled ? 0.6 : 1 }}>
      <Group justify="space-between" px="sm" py={6} wrap="wrap" gap="xs"
        style={{ borderBottom: '1px solid var(--mantine-color-default-border)' }}>
        <Group gap="xs" wrap="wrap">
          <Text fw={600} size="sm" tt="uppercase" style={{ letterSpacing: '0.03em' }}>{cls.group_name || 'Group'}</Text>
          <Text size="xs" c="dimmed">{cls.members.filter((m) => !m.excluded).length} items</Text>
          {cls.jita_saving > 0 && <Text size="xs" c="dimmed">· Jita saving up to {isk(cls.jita_saving)} per unit</Text>}
          {cls.in_fitting && <Badge size="xs" variant="light" color="warn">in fittings</Badge>}
          {cls.manual && <Badge size="xs" variant="light" color="info">edited</Badge>}
          {!cls.active && !cls.disabled && <Badge size="xs" variant="light" color="dimmed">fewer than 2 items</Badge>}
        </Group>
        <Switch size="xs" label="Interchangeable" checked={!cls.disabled}
          onChange={(e) => disable.mutate(!e.currentTarget.checked)} />
      </Group>
      <ScrollArea type="auto">
        <Table striped={false} verticalSpacing={4} miw={620}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Item</Table.Th>
              <Table.Th>Meta</Table.Th>
              <Table.Th ta="right">Type ID</Table.Th>
              <Table.Th ta="right">Jita sell</Table.Th>
              <Table.Th ta="right">C-J sell</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {cls.members.map((m) => (
              <Table.Tr key={m.type_id} style={{ opacity: m.excluded ? 0.5 : 1 }}>
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    <Text size="sm" td={m.excluded ? 'line-through' : undefined}>{m.type_name}</Text>
                    {m.in_fitting && <Badge size="xs" variant="dot" color="warn">fitted</Badge>}
                  </Group>
                </Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{meta(m)}</Text></Table.Td>
                <Table.Td ta="right"><Text size="xs" c="dimmed" ff="monospace">{m.type_id}</Text></Table.Td>
                <PriceCell value={m.jita_sell} cheapest={m.cheapest_jita} />
                <PriceCell value={m.home_sell} cheapest={m.cheapest_home}
                  note={m.home_repeats_jita ? 'no own quote' : undefined} />
                <Table.Td ta="right">
                  <Tooltip label={m.excluded ? 'Count this item as interchangeable again'
                    : m.manual ? 'Remove the item you added' : 'This item is not interchangeable for me'}>
                    <Button size="compact-xs" variant="subtle" color={m.excluded ? 'accent' : 'dimmed'}
                      onClick={() => exclude.mutate({ typeId: m.type_id, excluded: !m.excluded && !m.manual })}>
                      {m.excluded ? 'Include' : m.manual ? 'Remove' : 'Exclude'}
                    </Button>
                  </Tooltip>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
      <Group px="sm" py={4}><AddItem cls={cls} /></Group>
    </Card>
  )
}

export default function Equivalents() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['doctrine', 'equivalents'], queryFn: doctrineApi.equivalents })
  const [search, setSearch] = useState('')
  const [onlyFittings, setOnlyFittings] = useState(true)
  const [onlyGap, setOnlyGap] = useState(false)
  const [sort, setSort] = useState<string>('group')
  const [shown, setShown] = useState(PAGE)

  const classes = useMemo(() => data?.classes ?? [], [data])
  const filtered = useMemo(() => {
    const words = search.trim().toLowerCase().split(/\s+/).filter(Boolean)
    let xs = classes.filter((c) => (!onlyFittings || c.in_fitting) && (!onlyGap || c.jita_saving > 0)
      && words.every((w) => (c.group_name + ' ' + c.members.map((m) => m.type_name).join(' ')).toLowerCase().includes(w)))
    if (sort === 'saving') xs = [...xs].sort((a, b) => b.jita_saving - a.jita_saving)
    return xs
  }, [classes, search, onlyFittings, onlyGap, sort])

  if (isLoading) return <Center h={200}><Loader color="accent" /></Center>
  if (isError) return <Alert color="red" variant="light">Could not load the equivalent variants.</Alert>

  const items = classes.reduce((n, c) => n + c.members.length, 0)
  return (
    <Stack>
      <Title order={4}>Equivalents</Title>
      <Text size="sm" c="dimmed" maw={760}>
        Item groups with identical attributes, effects and fitting cost (CPU, powergrid, calibration), taken from
        the SDE. Any item in a group stands in for another: contracts with it stay valid, stockpile stock of it
        counts, and the Shopping List buys the cheapest one. Exclude an item, switch a group off, or add an item
        of the same item group yourself. Contracts are re-checked right after every change.
        The green price is the cheapest item of the group in that market.
      </Text>
      {data && !data.enabled && (
        <Alert color="warn" variant="light">
          Equivalent variants are switched off in Doctrine Settings - this list is not used right now.
        </Alert>
      )}
      <Group gap="lg">
        <Text size="xs" c="dimmed" ff="monospace">{classes.length} groups</Text>
        <Text size="xs" c="dimmed" ff="monospace">{items} items</Text>
        <Text size="xs" c="dimmed" ff="monospace">{classes.filter((c) => c.in_fitting).length} groups in your fittings</Text>
      </Group>
      <Group gap="sm" wrap="wrap">
        <TextInput leftSection={<IconSearch size={14} />} placeholder="Search items or groups, e.g. thermal hardener"
          value={search} onChange={(e) => { setSearch(e.currentTarget.value); setShown(PAGE) }}
          style={{ flex: '1 1 260px' }} />
        <Select w={190} data={[{ value: 'group', label: 'Sort by group' }, { value: 'saving', label: 'Sort by Jita saving' }]}
          value={sort} onChange={(v) => setSort(v ?? 'group')} allowDeselect={false} />
        <Switch label="Only in my fittings" checked={onlyFittings}
          onChange={(e) => { setOnlyFittings(e.currentTarget.checked); setShown(PAGE) }} />
        <Switch label="Only with a price gap" checked={onlyGap}
          onChange={(e) => { setOnlyGap(e.currentTarget.checked); setShown(PAGE) }} />
        <Text size="xs" c="dimmed" ml="auto">{filtered.length} shown</Text>
      </Group>
      {filtered.length === 0 && (
        <Text c="dimmed">
          {onlyFittings && classes.length > 0
            ? 'None of your fittings uses an item with an equivalent variant. Switch off "Only in my fittings" to see every group.'
            : 'No group matches this search.'}
        </Text>
      )}
      {filtered.slice(0, shown).map((c) => <ClassCard key={c.class_id} cls={c} />)}
      {filtered.length > shown && (
        <Button variant="light" w={220} onClick={() => setShown((n) => n + PAGE)}>
          Show {Math.min(PAGE, filtered.length - shown)} more
        </Button>
      )}
    </Stack>
  )
}
