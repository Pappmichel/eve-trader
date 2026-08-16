import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Stack, Button, Group, NumberInput, Select, SimpleGrid, ActionIcon, UnstyledButton } from '@mantine/core'
import { IconX } from '@tabler/icons-react'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { LogisticsRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { useAction } from '../../hooks/useAction'
import { qty } from '../../format'

export default function Logistics() {
  const { data: categories, isLoading: categoriesLoading } = useQuery({ queryKey: ['production', 'job-categories'], queryFn: productionApi.jobCategories })
  const { data: locations, isLoading: locationsLoading } = useQuery({ queryKey: ['production', 'category-locations'], queryFn: productionApi.categoryLocations })
  const { data: locationOptions } = useQuery({ queryKey: ['production', 'category-location-options'], queryFn: productionApi.categoryLocationOptions })
  const { data: structureNames } = useQuery({ queryKey: ['production', 'structure-names'], queryFn: productionApi.structureNames })
  const { data: rows, isError } = useQuery({
    queryKey: ['production', 'logistics'], queryFn: productionApi.logisticsStatus, retry: false,
  })

  const [draft, setDraft] = useState<Record<string, string>>({})
  // Only fills in categories *missing* from draft (a first load, or a newly
  // appeared category) - never overwrites an entry already there. Confirmed
  // real bug: this used to unconditionally replace the *whole* draft object
  // from `locations` on every change - since saveLocation/clearLocation both
  // invalidate the shared `category-locations` query, saving category A's
  // structure ID refetched `locations` and silently wiped whatever the user
  // had mid-typed (not yet saved) into category B's field, with no warning.
  useEffect(() => {
    if (!locations) return
    setDraft((d) => {
      const next = { ...d }
      for (const [cat, val] of Object.entries(locations)) {
        if (!(cat in next)) next[cat] = String(val)
      }
      return next
    })
  }, [locations])

  const saveLocation = useAction('Structure Saved', async (args: { category: string; locationId: number }) => {
    const result = await productionApi.setCategoryLocation(args.category, args.locationId)
    try {
      await productionApi.resolveStructureName(args.locationId)
    } catch {
      // Confirmed real bug: the location assignment above already succeeded
      // and persisted - a name-resolution failure (e.g. "No producer
      // character logged in yet") is a separate, best-effort step (the name
      // has its own manual "resolve" retry link once savedId is set) and
      // must not make this whole action report as a failure when the actual
      // save it was for went through fine.
    }
    return result
  }, [
    ['production', 'category-locations'], ['production', 'logistics'], ['production', 'structure-names'],
    ['production', 'category-location-options'],
  ])
  // Which category's Save/Remove button triggered the in-flight mutation -
  // saveLocation/clearLocation are each a single shared mutation instance
  // reused across every category's row (mapped below), so `.isPending` alone
  // can't tell them apart. Confirmed real bug: clicking Save for one category
  // put *every* category's Save button into the loading/disabled state for
  // the duration of that one request.
  const [pendingCategory, setPendingCategory] = useState<string | null>(null)
  const clearLocation = useAction('Structure Removed', productionApi.clearCategoryLocation, [
    ['production', 'category-locations'], ['production', 'logistics'],
  ])
  const resolveName = useAction('Name Resolved', (locationId: number) =>
    productionApi.resolveStructureName(locationId, true), [
    ['production', 'structure-names'],
  ])

  const grouped = useMemo(() => {
    const map = new Map<string, LogisticsRow[]>()
    for (const r of rows ?? []) {
      if (!map.has(r.category)) map.set(r.category, [])
      map.get(r.category)!.push(r)
    }
    return map
  }, [rows])

  const columns = useMemo<ColumnDef<LogisticsRow, any>[]>(() => [
    { header: 'Item', accessorKey: 'type_name', size: 260 },
    { header: 'Needed', accessorKey: 'needed', size: 120, cell: (i) => qty(i.getValue()) },
    { header: 'Available on Site', accessorKey: 'available', size: 150, cell: (i) => qty(i.getValue()) },
    {
      header: 'Missing', accessorKey: 'missing', size: 110,
      cell: (i) => <Text c={i.getValue() > 0 ? 'warn' : 'accent'} fw={i.getValue() > 0 ? 600 : 400}>{qty(i.getValue())}</Text>,
    },
  ], [])

  return (
    <Stack>
      <Card withBorder>
        <Title order={4} mb="xs">Structure per Category</Title>
        <Text size="xs" c="dimmed" mb="sm">
          Structure/location ID per job category (the system prefix in the structure name is irrelevant since it can
          change - only the ID counts). Leave empty for categories you don't want to track. The name is resolved
          automatically after saving - this needs at least one producer character with access to the structure
          (docking rights/having been there before) <b>and</b> the new ESI scope; characters added before this
          update need to be removed and re-added once for this to work.
        </Text>
        <SimpleGrid cols={3}>
          {(categories ?? []).map((cat) => {
            const savedId = locations?.[cat]
            const resolvedName = savedId != null ? structureNames?.[String(savedId)] : undefined
            const options = locationOptions?.[cat] ?? []
            return (
              <div key={cat}>
                {options.length > 1 && (
                  <Select
                    label={`${cat} - known locations`}
                    data={options.map((id) => ({
                      value: String(id),
                      label: structureNames?.[String(id)] ? `${structureNames[String(id)]} (${id})` : String(id),
                    }))}
                    value={savedId != null ? String(savedId) : null}
                    onChange={(v) => {
                      if (!v) return
                      setPendingCategory(cat)
                      saveLocation.mutate({ category: cat, locationId: Number(v) })
                    }}
                    mb={4}
                  />
                )}
                <Group align="flex-end" gap="xs">
                  <NumberInput
                    label={options.length > 1 ? 'Other/new structure ID' : cat}
                    placeholder="Structure ID"
                    value={draft[cat] ?? ''}
                    onChange={(v) => setDraft((d) => ({ ...d, [cat]: String(v) }))}
                    min={0}
                    hideControls
                    style={{ flex: 1 }}
                  />
                  <Button
                    size="xs" variant="default"
                    disabled={!draft[cat]}
                    loading={saveLocation.isPending && pendingCategory === cat}
                    onClick={() => {
                      setPendingCategory(cat)
                      saveLocation.mutate({ category: cat, locationId: Number(draft[cat]) })
                    }}
                  >
                    Save
                  </Button>
                  {savedId != null && (
                    <ActionIcon
                      size="lg" variant="subtle" color="danger" aria-label="Remove structure"
                      loading={clearLocation.isPending && pendingCategory === cat}
                      onClick={() => {
                        setPendingCategory(cat)
                        setDraft((d) => {
                          const next = { ...d }
                          delete next[cat]
                          return next
                        })
                        clearLocation.mutate(cat)
                      }}
                    >
                      <IconX size={16} />
                    </ActionIcon>
                  )}
                </Group>
                {savedId != null && (
                  <Text size="xs" c={resolvedName ? 'dimmed' : 'warn'} mt={4}>
                    {resolvedName ? resolvedName : (
                      <>
                        Name unknown -{' '}
                        <UnstyledButton
                          c="accent"
                          style={{ display: 'inline', textDecoration: 'underline', fontSize: 'var(--mantine-font-size-xs)' }}
                          onClick={() => resolveName.mutate(savedId)}
                        >
                          resolve
                        </UnstyledButton>
                      </>
                    )}
                  </Text>
                )}
              </div>
            )
          })}
        </SimpleGrid>
      </Card>

      {categoriesLoading || locationsLoading ? (
        <DataTable data={[]} columns={columns} isLoading maxHeight={320} />
      ) : isError ? (
        <HintCard>No build list computed yet - run <b>Compute Buy/Build List</b> in the Build List tab first.</HintCard>
      ) : !locations || Object.keys(locations).length === 0 ? (
        <HintCard>No category has a structure assigned yet - enter at least one structure ID above.</HintCard>
      ) : grouped.size === 0 ? (
        <HintCard>There are no jobs in the build list for the assigned categories right now.</HintCard>
      ) : (
        Array.from(grouped.entries()).map(([category, categoryRows]) => {
          const locationId = locations[category]
          const name = structureNames?.[String(locationId)]
          return (
            <Card withBorder key={category}>
              <Group justify="space-between" mb="xs">
                <Title order={5}>{category}</Title>
                <Text size="xs" c="dimmed">{name ? `${name} (${locationId})` : `Structure ID ${locationId}`}</Text>
              </Group>
              <DataTable data={categoryRows} columns={columns} maxHeight={320} />
            </Card>
          )
        })
      )}
    </Stack>
  )
}
