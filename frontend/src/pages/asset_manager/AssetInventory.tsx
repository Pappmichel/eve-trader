// Inventory tab: every row of every source, filtered, grouped, sorted and
// paged on the server (docs/ASSET_MANAGER_PLAN.md D8). Totals cover the
// whole filter, not the page.
import { useEffect, useMemo, useState } from 'react'
import {
  ActionIcon, Anchor, Button, Card, Chip, Group, Menu, Modal, MultiSelect, Pagination, Paper,
  SegmentedControl, Select, SimpleGrid, Skeleton, Stack, Switch, Table, Text, TextInput, Tooltip, UnstyledButton,
} from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { IconArrowDown, IconArrowUp, IconDots, IconDownload, IconSearch, IconShip, IconTrash } from '@tabler/icons-react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { assetManagerApi } from '../../api/client'
import type { AssetQuery, AssetRow } from '../../api/types'
import { HintCard } from '../../components/HintCard'
import { isk, qty, volume } from '../../format'
import { notify } from '../../notify'
import { AssetBadges, WantedBy } from './common'
import { DEFAULT_QUERY, SOURCE_LABELS, filterPart, paramsFromQuery, queryFromParams } from './assetQuery'
import { ShipDrawer, type ShipRef } from './ShipDrawer'
import { TypeDetailDrawer } from './TypeDetailDrawer'

const ALL_SOURCES = ['esi', 'manual', 'listed', 'contract', 'in_production']
const HIDEABLE_BADGES = ['asset_safety', 'delivery', 'corp_market', 'assembled', 'fitted', 'bpc', 'bpo']

interface Column {
  key: string
  label: string
  sort?: string
  align?: 'right'
  render: (row: AssetRow) => React.ReactNode
}

// Why a place has no name (location registry status, docs/LOCATION_RESOLUTION_PLAN.md).
const LOOKUP_HINT: Record<string, string> = {
  forbidden: 'no access', not_found: 'not found', transient: 'lookup failed, will retry',
  pending: 'not looked up yet',
}
const STRUCTURE_ID_MIN = 1_000_000_000_000

function locationLabel(row: Pick<AssetRow, 'location_id' | 'location_name' | 'location_status'>): string {
  if (row.location_name) return row.location_name
  if (!row.location_id) return 'No location'
  const hint = row.location_status ? LOOKUP_HINT[row.location_status] : undefined
  if (!hint) return `Location ${row.location_id}`
  return `${row.location_id >= STRUCTURE_ID_MIN ? 'Structure' : 'Location'} ${row.location_id} (${hint})`
}

// Name, re-check or un-name a player structure the app could not name itself.
function LocationActions({ locationId }: { locationId: number }) {
  const queryClient = useQueryClient()
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState('')
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['asset-manager'] })
  const fail = (title: string) => (e: unknown) => notify({ title, message: String((e as Error).message), color: 'danger' })
  const save = useMutation({
    mutationFn: () => assetManagerApi.setLocationName(locationId, name),
    onSuccess: () => { setNaming(false); setName(''); refresh() },
    onError: fail('Name location'),
  })
  const remove = useMutation({
    mutationFn: () => assetManagerApi.removeLocationName(locationId),
    onSuccess: refresh,
    onError: fail('Remove name'),
  })
  const retry = useMutation({
    mutationFn: () => assetManagerApi.retryLocation(locationId),
    onSuccess: (r) => {
      notify({ title: 'Look up again', message: r.name ? `Found: ${r.name}` : `Still unnamed (${r.status ?? 'unknown'})` })
      refresh()
    },
    onError: fail('Look up again'),
  })
  return (
    <>
      <Menu position="bottom-start" withinPortal>
        <Menu.Target>
          <ActionIcon size="xs" variant="subtle" aria-label="Location actions" loading={retry.isPending}>
            <IconDots size={12} />
          </ActionIcon>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item onClick={() => setNaming(true)}>Name this location…</Menu.Item>
          <Menu.Item onClick={() => retry.mutate()}>Look up again</Menu.Item>
          <Menu.Item onClick={() => remove.mutate()}>Remove my name</Menu.Item>
        </Menu.Dropdown>
      </Menu>
      <Modal opened={naming} onClose={() => setNaming(false)} title="Name this location" size="sm">
        <Stack>
          <Text size="xs" c="dimmed">
            Only you see this name. A name EVE gives the structure later still wins.
          </Text>
          <TextInput label="Name" value={name} maxLength={100} data-autofocus
            onChange={(e) => setName(e.currentTarget.value)} />
          <Group justify="flex-end">
            <Button size="xs" onClick={() => save.mutate()} loading={save.isPending} disabled={!name.trim()}>Save</Button>
          </Group>
        </Stack>
      </Modal>
    </>
  )
}

function SortHeader({ col, query, onSort }: { col: Column; query: AssetQuery; onSort: (key: string) => void }) {
  if (!col.sort) return <Table.Th ta={col.align}>{col.label}</Table.Th>
  const active = query.sort === col.sort
  return (
    <Table.Th ta={col.align}>
      <UnstyledButton onClick={() => onSort(col.sort as string)}>
        <Group gap={2} wrap="nowrap" justify={col.align === 'right' ? 'flex-end' : 'flex-start'}>
          <Text size="xs" fw={700}>{col.label}</Text>
          {active && (query.desc ? <IconArrowDown size={12} /> : <IconArrowUp size={12} />)}
        </Group>
      </UnstyledButton>
    </Table.Th>
  )
}

function Totals({ totals, unpricedHint }: { totals: { value: number; volume: number; stacks: number; types: number; locations: number; unpriced: number }; unpricedHint: string }) {
  const items: [string, string, string | null][] = [
    ['Value', isk(totals.value),
      'Every source selected above, including listed, contracted and in-production items. Untick those for what physically sits in hangars.'],
    ['Volume', volume(totals.volume), null], ['Stacks', qty(totals.stacks), null],
    ['Types', qty(totals.types), null], ['Locations', qty(totals.locations), null],
  ]
  return (
    <SimpleGrid cols={{ base: 2, sm: 3, md: 6 }} spacing="xs">
      {items.map(([label, value, hint]) => (
        <Tooltip key={label} label={hint} disabled={!hint} multiline w={260} withArrow>
          <Paper p="xs" withBorder>
            <Text size="xs" c="dimmed">{label}</Text>
            <Text fw={600}>{value}</Text>
          </Paper>
        </Tooltip>
      ))}
      <Tooltip label={unpricedHint} multiline w={260} withArrow>
        <Paper p="xs" withBorder>
          <Text size="xs" c="dimmed">Unpriced stacks</Text>
          <Text fw={600} c={totals.unpriced ? 'orange' : undefined}>{qty(totals.unpriced)}</Text>
        </Paper>
      </Tooltip>
    </SimpleGrid>
  )
}

export default function AssetInventory() {
  const [params, setParams] = useSearchParams()
  const query = useMemo(() => queryFromParams(params), [params])
  const [text, setText] = useState(query.q ?? '')
  const [debounced] = useDebouncedValue(text, 300)
  const [typeId, setTypeId] = useState<number | null>(null)
  const [ship, setShip] = useState<ShipRef | null>(null)
  const [saveOpen, setSaveOpen] = useState(false)
  const [viewName, setViewName] = useState('')
  const queryClient = useQueryClient()

  const update = (patch: AssetQuery, resetPage = true) => {
    const next = { ...query, ...patch }
    if (resetPage) next.page = 1
    setParams(paramsFromQuery(next), { replace: false })
  }

  useEffect(() => {
    if ((query.q ?? '') !== debounced) update({ q: debounced || undefined })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced])

  // The URL changed from outside the search box (back button, saved view):
  // show its text instead of the stale input.
  useEffect(() => {
    if ((query.q ?? '') !== debounced) setText(query.q ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.q])

  const items = useQuery({
    queryKey: ['asset-manager', 'items', query],
    queryFn: () => assetManagerApi.items(query),
    placeholderData: keepPreviousData,
  })
  const views = useQuery({ queryKey: ['asset-manager', 'views'], queryFn: assetManagerApi.views })
  const saveView = useMutation({
    mutationFn: () => assetManagerApi.saveView(viewName.trim(), filterPart(query)),
    onSuccess: () => {
      setSaveOpen(false)
      queryClient.invalidateQueries({ queryKey: ['asset-manager', 'views'] })
      notify({ title: 'View saved', message: viewName.trim(), color: 'accent' })
    },
    onError: (e) => notify({ title: 'Save view', message: String((e as Error).message), color: 'danger' }),
  })
  const deleteView = useMutation({
    mutationFn: (id: number) => assetManagerApi.deleteView(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['asset-manager', 'views'] }),
  })

  const data = items.data
  const facets = data?.facets
  const groupBy = query.group_by ?? 'type_location'

  const columns = useMemo<Column[]>(() => {
    const type: Column = {
      key: 'type', label: 'Item', sort: 'type_name',
      render: (r) => (
        <Stack gap={0}>
          <Anchor size="sm" component="button" ta="left" onClick={() => setTypeId(r.type_id)}>
            {r.item_name ? `${r.item_name} (${r.type_name})` : r.type_name}
          </Anchor>
          <Text size="xs" c="dimmed">{r.category_name}{r.group_name ? ` / ${r.group_name}` : ''}</Text>
        </Stack>
      ),
    }
    const place: Column = {
      key: 'location', label: 'Location', sort: 'location_name',
      render: (r) => (
        <Stack gap={0}>
          <Group gap={4} wrap="nowrap">
            <Text size="sm">{locationLabel(r)}</Text>
            {r.location_id != null && r.location_id >= STRUCTURE_ID_MIN && <LocationActions locationId={r.location_id} />}
          </Group>
          <Text size="xs" c="dimmed">{[r.solar_system_name, r.region_name].filter(Boolean).join(' · ')}</Text>
        </Stack>
      ),
    }
    const where: Column = {
      key: 'where', label: 'Hangar / container',
      render: (r) => <Text size="xs">{[r.hangar, ...(r.container_path ?? [])].filter(Boolean).join(' › ')}</Text>,
    }
    const owner: Column = {
      key: 'owner', label: groupBy === 'none' ? 'Owner' : 'Owners', sort: 'owner_name',
      render: (r) => <Text size="xs">{groupBy === 'none' ? r.owner_name : r.owners.join(', ')}</Text>,
    }
    const badges: Column = {
      key: 'badges', label: 'Tags',
      render: (r) => (
        <Group gap={4} wrap="wrap" miw={120}>
          <AssetBadges badges={r.badges} />
          <WantedBy wanted={r.wanted_by} />
          {r.badges.includes('assembled') && r.owner_type && r.item_id && (
            <Tooltip label="Open ship view" withArrow>
              <ActionIcon size="sm" variant="subtle" aria-label="Open ship view"
                onClick={() => setShip({ ownerType: r.owner_type as string, ownerId: r.owner_id as number, itemId: r.item_id as number })}>
                <IconShip size={14} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      ),
    }
    const quantity: Column = { key: 'quantity', label: 'Quantity', sort: 'quantity', align: 'right', render: (r) => qty(r.quantity) }
    const price: Column = { key: 'unit_price', label: 'Unit price', sort: 'unit_price', align: 'right', render: (r) => isk(r.unit_price) }
    const value: Column = {
      key: 'value', label: 'Value', sort: 'value', align: 'right',
      render: (r) => (r.value == null ? <Text size="sm" c="dimmed">–</Text>
        : <Text size="sm">{isk(r.value)}{r.unpriced ? <Text span size="xs" c="orange"> *</Text> : null}</Text>),
    }
    const vol: Column = { key: 'volume', label: 'Volume', sort: 'volume', align: 'right', render: (r) => volume(r.volume) }
    const places: Column = { key: 'places', label: 'Places', align: 'right', render: (r) => qty(r.location_count ?? 0) }
    if (groupBy === 'type') return [type, places, owner, badges, quantity, price, value, vol]
    if (groupBy === 'type_location') return [type, place, owner, badges, quantity, price, value, vol]
    return [type, place, where, owner, badges, quantity, price, value, vol]
  }, [groupBy])

  const onSort = (key: string) => update({ sort: key, desc: query.sort === key ? !query.desc : true }, false)

  const systemOptions = (facets?.systems ?? [])
    .filter((s) => !query.region_id || s.region_id === query.region_id)
    .map((s) => ({ value: String(s.id), label: `${s.name ?? s.id}${s.security != null ? ` (${s.security.toFixed(1)})` : ''}` }))
  const locationOptions = (facets?.locations ?? [])
    .filter((l) => (!query.region_id || l.region_id === query.region_id) && (!query.system_id || l.solar_system_id === query.system_id))
    .map((l) => ({ value: String(l.id), label: l.name ?? (l.id ? `Location ${l.id}` : 'No location') }))

  const copyEveList = async () => {
    try {
      const textList = await assetManagerApi.eveList(filterPart(query))
      await navigator.clipboard.writeText(textList)
      notify({ title: 'Copied', message: 'Type and quantity per line - paste into multibuy or an appraisal site.', color: 'accent' })
    } catch (e) {
      notify({ title: 'Copy EVE list', message: String((e as Error).message), color: 'danger' })
    }
  }

  const activeView = views.data?.views.find((v) => paramsFromQuery({ ...DEFAULT_QUERY, ...v.query }).toString() === paramsFromQuery(filterPart(query)).toString())

  return (
    <Stack>
      {data && !data.shared && (
        <HintCard>
          No character shares assets with a tool you hold. Open the Characters page and share Assets (and,
          for the extra sources, Blueprints, Market Orders, Contracts and Industry Jobs) with the Asset Manager.
        </HintCard>
      )}

      <Card withBorder p="sm">
        <Stack gap="xs">
          <Group gap="xs" align="flex-end" wrap="wrap">
            <TextInput label="Search" placeholder="Item, container, location, owner…" leftSection={<IconSearch size={14} />}
              value={text} onChange={(e) => setText(e.currentTarget.value)} w={260} />
            <MultiSelect label="Owners" placeholder="All" w={240} clearable searchable
              data={(facets?.owners ?? []).map((o) => ({ value: o.key as string, label: o.name ?? String(o.key) }))}
              value={query.owners ?? []} onChange={(v) => update({ owners: v })} />
            <Select label="Region" placeholder="All" w={170} clearable searchable
              data={(facets?.regions ?? []).map((r) => ({ value: String(r.id), label: r.name ?? String(r.id) }))}
              value={query.region_id != null ? String(query.region_id) : null}
              onChange={(v) => update({ region_id: v ? Number(v) : null, system_id: null, location_id: null })} />
            <Select label="System" placeholder="All" w={170} clearable searchable data={systemOptions}
              value={query.system_id != null ? String(query.system_id) : null}
              onChange={(v) => update({ system_id: v ? Number(v) : null, location_id: null })} />
            <Select label="Location" placeholder="All" w={240} clearable searchable data={locationOptions}
              value={query.location_id != null ? String(query.location_id) : null}
              onChange={(v) => update({ location_id: v ? Number(v) : null })} />
            <MultiSelect label="Category" placeholder="All" w={200} clearable searchable
              data={(facets?.categories ?? []).map((c) => ({ value: String(c.id), label: c.name ?? String(c.id) }))}
              value={(query.category_ids ?? []).map(String)} onChange={(v) => update({ category_ids: v.map(Number) })} />
          </Group>
          <Group gap="md" align="center" wrap="wrap">
            <Chip.Group multiple value={query.sources?.length ? query.sources : ALL_SOURCES}
              onChange={(v) => update({ sources: v.length === ALL_SOURCES.length ? [] : v })}>
              <Group gap={4}>
                {ALL_SOURCES.filter((s) => s !== 'manual' || data?.manual_stock_visible).map((s) => (
                  <Chip key={s} value={s} size="xs">{SOURCE_LABELS[s]}</Chip>
                ))}
              </Group>
            </Chip.Group>
            <MultiSelect placeholder="Hide tags…" w={220} size="xs" clearable
              data={HIDEABLE_BADGES.map((b) => ({ value: b, label: b.replace('_', ' ') }))}
              value={query.hide_badges ?? []} onChange={(v) => update({ hide_badges: v })} />
            <Tooltip label="Only what the planners count as stock: no Asset Safety, deliveries, assembled ships, fittings, listed or contracted items." multiline w={280}>
              <Switch size="xs" label="Stock only" checked={!!query.stock_only} onChange={(e) => update({ stock_only: e.currentTarget.checked })} />
            </Tooltip>
            <SegmentedControl size="xs" value={groupBy}
              onChange={(v) => update({ group_by: v as AssetQuery['group_by'] })}
              data={[{ value: 'type_location', label: 'Per item & place' }, { value: 'type', label: 'Per item' }, { value: 'none', label: 'Every stack' }]} />
          </Group>
          <Group gap="xs" wrap="wrap">
            <Select size="xs" w={220} placeholder="Saved views" clearable
              data={(views.data?.views ?? []).map((v) => ({ value: String(v.id), label: v.error ? `${v.name} (outdated)` : v.name }))}
              value={activeView ? String(activeView.id) : null}
              onChange={(v) => {
                const view = views.data?.views.find((x) => String(x.id) === v)
                setParams(paramsFromQuery({ ...DEFAULT_QUERY, ...(view?.query ?? {}) }))
                setText(view?.query.q ?? '')
              }} />
            <Button size="xs" variant="light" onClick={() => { setViewName(activeView?.name ?? ''); setSaveOpen(true) }}>Save view</Button>
            {activeView && (
              <Tooltip label="Delete this saved view" withArrow>
                <ActionIcon size="md" variant="subtle" color="red" aria-label="Delete saved view" onClick={() => deleteView.mutate(activeView.id)}>
                  <IconTrash size={14} />
                </ActionIcon>
              </Tooltip>
            )}
            <Button size="xs" variant="subtle" onClick={() => { setText(''); setParams(new URLSearchParams()) }}>Reset filters</Button>
            <Menu>
              <Menu.Target>
                <Button size="xs" variant="light" leftSection={<IconDownload size={14} />}>Export</Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item component="a" href={assetManagerApi.csvUrl(filterPart(query))}>CSV (current filter)</Menu.Item>
                <Menu.Item onClick={copyEveList}>Copy as EVE list</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Stack>
      </Card>

      {data ? (
        <Totals totals={data.totals}
          unpricedHint="Stacks without a market or manual price (e.g. blueprint copies without an invention recipe). They are left out of the value, never guessed - set a manual price on the Portfolio page." />
      ) : <Skeleton h={60} />}

      <Paper withBorder style={{ overflowX: 'auto' }}>
        <Table striped highlightOnHover fz="sm" miw={900} style={{ opacity: items.isFetching ? 0.6 : 1 }}>
          <Table.Thead>
            <Table.Tr>{columns.map((c) => <SortHeader key={c.key} col={c} query={query} onSort={onSort} />)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.isLoading && <Table.Tr><Table.Td colSpan={columns.length}><Skeleton h={120} /></Table.Td></Table.Tr>}
            {items.isError && (
              <Table.Tr><Table.Td colSpan={columns.length}>
                <Text c="red" size="sm">{String((items.error as Error).message)}</Text>
              </Table.Td></Table.Tr>
            )}
            {data?.rows.length === 0 && (
              <Table.Tr><Table.Td colSpan={columns.length}><Text c="dimmed" size="sm">Nothing matches this filter.</Text></Table.Td></Table.Tr>
            )}
            {data?.rows.map((row) => (
              <Table.Tr key={row.key}>
                {columns.map((c) => <Table.Td key={c.key} ta={c.align}>{c.render(row)}</Table.Td>)}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Paper>

      {data && (
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            {qty(data.total_rows)} rows{data.totals.unpriced ? ' · * value leaves out unpriced stacks' : ''}
          </Text>
          <Group gap="xs">
            <Select size="xs" w={90} data={['25', '50', '100', '250']} value={String(query.page_size ?? 50)}
              onChange={(v) => update({ page_size: Number(v ?? 50) })} aria-label="Rows per page" />
            <Pagination size="sm" value={query.page ?? 1} total={Math.max(1, Math.ceil(data.total_rows / data.page_size))}
              onChange={(p) => update({ page: p }, false)} />
          </Group>
        </Group>
      )}

      <Modal opened={saveOpen} onClose={() => setSaveOpen(false)} title="Save view">
        <Stack>
          <TextInput label="Name" value={viewName} maxLength={80} onChange={(e) => setViewName(e.currentTarget.value)} data-autofocus />
          <Text size="xs" c="dimmed">Saves the current filters, grouping and sort. A view with the same name is replaced.</Text>
          <Group justify="flex-end">
            <Button onClick={() => saveView.mutate()} loading={saveView.isPending} disabled={!viewName.trim()}>Save</Button>
          </Group>
        </Stack>
      </Modal>

      <TypeDetailDrawer typeId={typeId} onClose={() => setTypeId(null)} />
      <ShipDrawer ship={ship} onClose={() => setShip(null)} />
    </Stack>
  )
}
