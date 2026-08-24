import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Title, Text, Group, Select, Button, Stack, Badge } from '@mantine/core'
import type { ColumnDef } from '@tanstack/react-table'

import { productionApi } from '../../api/client'
import type { InventionNeedRow, InventionResult, T1BpcInventionNeedRow } from '../../api/types'
import { DataTable } from '../../components/DataTable'
import { HintCard } from '../../components/HintCard'
import { SearchableSelect } from '../../components/SearchableSelect'
import { useAction } from '../../hooks/useAction'
import { useItemNameOptions } from '../../hooks/useStaticOptions'
import { isk, pct, qty } from '../../format'

export default function Invention() {
  const { data: sdeCounts, isLoading: sdeLoading, isError: sdeError, refetch: refetchSde } =
    useQuery({ queryKey: ['production', 'sde', 'counts'], queryFn: productionApi.sdeCounts })
  const { data: decryptors } = useQuery({ queryKey: ['production', 'decryptors'], queryFn: productionApi.decryptors })
  const { data: settings } = useQuery({ queryKey: ['production', 'settings'], queryFn: productionApi.settings })
  const { data: plan, isLoading: planLoading, isError: planError, refetch: refetchPlan, dataUpdatedAt: planUpdatedAt } =
    useQuery({ queryKey: ['production', 'plan'], queryFn: productionApi.plan })
  const inventionNeeds = plan?.invention_list ?? []
  const {
    data: t1BpcNeeds, isLoading: t1BpcLoading, isError: t1BpcError, refetch: refetchT1Bpc,
    dataUpdatedAt: t1BpcUpdatedAt,
  } = useQuery({
    queryKey: ['production', 'invention', 't1-bpc-needs'], queryFn: productionApi.t1BpcInventionNeeds, retry: false,
  })

  const refreshPlan = useAction('Refresh Production', productionApi.refreshPlan, [
    ['production', 'plan'], ['production', 'stock-targets'], ['production', 'logistics'],
    ['production', 'invention', 't1-bpc-needs'],
  ])

  const { data: itemNameOptions } = useItemNameOptions()
  const blueprintOptions = useMemo(
    () => (itemNameOptions ?? []).map((t) => ({ value: String(t.type_id), label: t.type_name })),
    [itemNameOptions],
  )
  const [productId, setProductId] = useState<string | null>(null)
  const productName = blueprintOptions.find((o) => o.value === productId)?.label ?? ''
  const [decryptorChoice, setDecryptorChoice] = useState('Compare all')
  const [results, setResults] = useState<InventionResult[] | null>(null)

  const estimate = useAction('Compute Invention', () =>
    productionApi.estimateInvention(productName, decryptorChoice === 'Compare all' ? null : decryptorChoice))

  const sdeReady = sdeCounts && Object.values(sdeCounts).some((c) => c > 0)

  const needsColumns = useMemo<ColumnDef<InventionNeedRow, any>[]>(() => [
    { header: 'T2 Item', accessorKey: 'type_name', size: 210 },
    { header: 'T1 Blueprint', accessorKey: 't1_blueprint_name', size: 210 },
    { header: 'Decryptor', accessorKey: 'decryptor', size: 130 },
    { header: 'Success Chance', accessorKey: 'probability', size: 140, cell: (i) => pct(i.getValue()) },
    { header: 'Runs/BPC', accessorKey: 'output_runs', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Runs Needed', accessorKey: 'runs_needed', size: 130, cell: (i) => qty(i.getValue()) },
    { header: 'BPCs Needed', accessorKey: 'bpcs_needed', size: 130, cell: (i) => qty(i.getValue()) },
    { header: 'Recommended Invention Runs', accessorKey: 'recommended_invention_runs', size: 200, cell: (i) => qty(i.getValue()) },
    { header: 'T2 BPCs Owned', accessorKey: 't2_bpc_owned', size: 140, cell: (i) => qty(i.getValue()) },
    {
      header: 'Stockpile %', accessorKey: 'stockpile_pct', size: 120,
      cell: (i) => {
        const v = i.getValue() as number
        const color = v >= 50 ? 'accent' : v > 0 ? 'warn' : 'gray'
        return <Badge color={color} variant="light">{v.toFixed(0)}%</Badge>
      },
    },
  ], [])

  const t1BpcColumns = useMemo<ColumnDef<T1BpcInventionNeedRow, any>[]>(() => [
    { header: 'T1 Blueprint', accessorKey: 'name', size: 220 },
    { header: 'Needed', accessorKey: 'needed', size: 110, cell: (i) => qty(i.getValue()) },
    { header: 'Available on Site', accessorKey: 'available', size: 150, cell: (i) => qty(i.getValue()) },
    {
      header: 'Missing', accessorKey: 'missing', size: 110,
      cell: (i) => <Text c={i.getValue() > 0 ? 'warn' : 'accent'} fw={i.getValue() > 0 ? 600 : 400}>{qty(i.getValue())}</Text>,
    },
    {
      header: 'BPO On Site', accessorKey: 'bpo_present', size: 130,
      cell: (i) => (i.getValue() ? 'Yes' : 'No'),
    },
  ], [])

  const columns = useMemo<ColumnDef<InventionResult, any>[]>(() => [
    { header: 'Decryptor', accessorKey: 'decryptor', size: 130 },
    { header: 'Success Chance', accessorKey: 'probability', size: 140, cell: (i) => pct(i.getValue()) },
    { header: 'BPC Runs', accessorKey: 'output_runs', size: 100, cell: (i) => qty(i.getValue()) },
    { header: 'ME', accessorKey: 'me', size: 70 },
    { header: 'TE', accessorKey: 'te', size: 70 },
    { header: 'Datacore Cost', accessorKey: 'datacore_cost', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Decryptor Cost', accessorKey: 'decryptor_cost', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Cost/Success', accessorKey: 'expected_cost_per_success', size: 140, cell: (i) => isk(i.getValue()) },
    { header: 'Cost/Run', accessorKey: 'expected_cost_per_run', size: 130, cell: (i) => isk(i.getValue()) },
    { header: 'ME Savings/Run', accessorKey: 'material_savings_per_run', size: 150, cell: (i) => isk(i.getValue()) },
    { header: 'Net Cost/Run', accessorKey: 'net_cost_per_run', size: 140, cell: (i) => isk(i.getValue()) },
  ], [])

  if (sdeLoading) return <DataTable data={[]} columns={needsColumns} isLoading maxHeight={360} />
  if (sdeError) return <DataTable data={[]} columns={needsColumns} isError onRetry={() => refetchSde()} maxHeight={360} />
  if (!sdeReady) return <HintCard>SDE cache is empty. Click <b>Refresh SDE</b> in the Admin tool.</HintCard>

  const best = results?.[0]

  return (
    <Stack>
      {settings && (
        <Text size="xs" c="dimmed">
          Skill assumption: Encryption Lv.{settings.encryption_skill_level}, Datacore skills Lv.
          {settings.datacore_skill_1_level}/{settings.datacore_skill_2_level} (adjustable in Settings).
        </Text>
      )}

      <Card withBorder>
        <Group justify="space-between" align="center" mb="xs">
          <Title order={4}>Required Invention Runs (all stock targets)</Title>
          <Button variant="default" size="xs" onClick={() => refreshPlan.mutate()} loading={refreshPlan.isPending}>
            Recompute
          </Button>
        </Group>
        {planLoading ? (
          <DataTable data={[]} columns={needsColumns} isLoading maxHeight={360} />
        ) : planError ? (
          <DataTable data={[]} columns={needsColumns} isError onRetry={() => refetchPlan()} maxHeight={360} />
        ) : !plan ? (
          <HintCard>No computation yet - click <b>Recompute</b>.</HintCard>
        ) : inventionNeeds.length === 0 ? (
          <HintCard>No Tech II stock target with an invention recipe is configured.</HintCard>
        ) : (
          <>
            <DataTable data={inventionNeeds} columns={needsColumns} maxHeight={360} dataUpdatedAt={planUpdatedAt} />
            <Text size="xs" c="dimmed" mt="xs">
              Covers <b>all</b> Tech II items set up as stock targets, not just the current build list - including
              ones that are currently fully stocked (0 runs) or where the market is currently cheaper than building
              it yourself. Runs needed = missing quantity to the stock target ÷ quantity per manufacturing run,
              rounded up. BPCs needed = runs needed ÷ runs/BPC, rounded up. Recommended invention runs = BPCs needed
              ÷ success chance, rounded up - expected number of invention jobs to get enough BPCs. Decryptor as in
              the Stock Targets tab (manual override possible). T2 BPCs Owned = copies of this row's own invented
              blueprint currently owned anywhere, not just at the invention station. Stockpile % = current stock ÷
              backup target, capped at 100%.
            </Text>
          </>
        )}
      </Card>

      <Card withBorder>
        <Title order={4} mb="xs">T1 BPC Runs Needed at Invention Station</Title>
        {t1BpcLoading ? (
          <DataTable data={[]} columns={t1BpcColumns} isLoading maxHeight={360} />
        ) : t1BpcError ? (
          <DataTable data={[]} columns={t1BpcColumns} isError onRetry={() => refetchT1Bpc()} maxHeight={360} />
        ) : !t1BpcNeeds || t1BpcNeeds.length === 0 ? (
          <HintCard>
            Nothing currently needs inventing, or no invention station is configured (Logistics tab's
            "Invention structure ID").
          </HintCard>
        ) : (
          <>
            <DataTable data={t1BpcNeeds} columns={t1BpcColumns} maxHeight={360} dataUpdatedAt={t1BpcUpdatedAt} />
            <Text size="xs" c="dimmed" mt="xs">
              Just the T1 blueprint copies from the table above - the Logistics tab's combined Invention section also
              mixes in decryptors and datacores, which made "how many BPC runs am I actually short" hard to see at a
              glance. BPO On Site = whether an original BPO for that blueprint sits at the invention station too, so
              a missing copy can be reprinted on site instead of imported.
            </Text>
          </>
        )}
      </Card>

      <Card withBorder>
        <Group grow align="flex-end">
          <SearchableSelect
            label="T2/T3 blueprint name"
            placeholder="Search blueprint…"
            data={blueprintOptions}
            value={productId}
            onChange={setProductId}
          />
          <Select label="Decryptor" data={['Compare all', ...(decryptors ?? [])]} value={decryptorChoice} onChange={(v) => v && setDecryptorChoice(v)} />
          <Button
            loading={estimate.isPending}
            disabled={!productName}
            onClick={() => estimate.mutate(undefined, { onSuccess: (r) => setResults(r) })}
          >
            Compute
          </Button>
        </Group>
      </Card>

      {results && best ? (
        <>
          <Card withBorder padding="sm" w={400}>
            <Text size="xs" c="dimmed" tt="uppercase">{best.t1_blueprint_name} → {best.product_name}</Text>
            <Title order={3} c="accent">{best.decryptor}</Title>
          </Card>
          <DataTable data={results} columns={columns} maxHeight={400} />
          <Text size="xs" c="dimmed">
            Net cost/run = cost/run minus the material savings this decryptor's ME gives when building from this
            BPC - 'Best' is then chosen the same way as in the build list.
          </Text>
        </>
      ) : (
        <HintCard>Enter the name of the <b>desired</b> T2/T3 blueprint (not the T1 blueprint) and click <b>Compute</b>.</HintCard>
      )}
    </Stack>
  )
}
