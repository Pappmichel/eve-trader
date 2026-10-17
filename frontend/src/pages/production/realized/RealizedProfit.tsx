import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Button, Group, SegmentedControl, Stack, Tabs, Text, TextInput, Tooltip } from '@mantine/core'
import { modals } from '@mantine/modals'

import { ApiError, charactersApi, productionApi } from '../../../api/client'
import type { RealizedSummary } from '../../../api/types'
import { HintCard } from '../../../components/HintCard'
import { JobProgress } from '../../../components/JobProgress'
import { useBackgroundJob, useBackgroundJobStart } from '../../../hooks/useBackgroundJob'
import { useStructureNameOptions } from '../../../hooks/useStaticOptions'
import { notify } from '../../../notify'
import { dateTime, isk } from '../../../format'
import { InventoryTab } from './InventoryTab'
import { JobsTab } from './JobsTab'
import { LedgerTab } from './LedgerTab'
import { OverviewTab } from './OverviewTab'
import { ProductsTab } from './ProductsTab'
import { WarningsTab } from './WarningsTab'
import {
  REALIZED_TABS, readRealizedTab, writeRealizedTab,
  type RealizedTabId, type WarningFocus,
} from './labels'
import {
  DEFAULT_PERIOD, claimAutoRebuild, customRangeError, holdAutoRebuild,
  realizedPeriodSearch, scheduleAutoRebuildReset, shouldAutoRecompute,
  type RealizedPeriod,
} from './period'

const REBUILD_HINT = 'Recompute to build realized profit from the archive.'
const PERIOD_OPTIONS = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: 'since_t0', label: 'Since T0' },
  { value: 'custom', label: 'Custom' },
]

type Preset = '7' | '30' | '90' | 'since_t0' | 'custom'
type Focus = { typeId: number } | { jobRef: string } | null

function useQueryError(title: string, isError: boolean, error: unknown) {
  const reported = useRef(false)
  useEffect(() => {
    if (!isError) {
      reported.current = false
      return
    }
    if (reported.current) return
    reported.current = true
    const message = error instanceof ApiError ? error.message : 'Failed to load.'
    notify({ title: `${title} - Error`, message, color: 'danger' })
  }, [title, isError, error])
}

function activePeriod(preset: Preset, custom: { start: string; end: string } | null): RealizedPeriod | null {
  if (preset === 'custom') return custom ? { mode: 'custom', start: custom.start, end: custom.end } : null
  if (preset === 'since_t0') return { mode: 'since_t0' }
  if (preset === '7' || preset === '30' || preset === '90') return { mode: 'days', days: Number(preset) as 7 | 30 | 90 }
  return DEFAULT_PERIOD
}

function statusLine(summary: RealizedSummary): string {
  const status = summary.status
  const t0 = status.cutover_t0 ? dateTime(status.cutover_t0) : 'unknown'
  return [
    `Archive since ${dateTime(status.archive_since)}`,
    `T0 ${t0}`,
    `Last recompute ${dateTime(status.last_recompute)}`,
    `Wallet sync ${dateTime(status.last_wallet_sync)}`,
    `Jobs sync ${dateTime(status.last_jobs_sync)}`,
  ].join(' · ')
}

export default function RealizedProfit() {
  const [preset, setPreset] = useState<Preset>('30')
  const [draftStart, setDraftStart] = useState('')
  const [draftEnd, setDraftEnd] = useState('')
  const [custom, setCustom] = useState<{ start: string; end: string } | null>(null)
  const [tab, setTab] = useState<RealizedTabId>(readRealizedTab)
  const [focus, setFocus] = useState<Focus>(null)
  const [offer, setOffer] = useState<string | null>(null)
  const [freightBusy, setFreightBusy] = useState(false)

  const period = activePeriod(preset, custom)
  const periodQuery = period ? realizedPeriodSearch(period) : ''
  const { data: locationNames } = useStructureNameOptions()

  const summaryQuery = useQuery({
    queryKey: ['production', 'realized', 'summary', periodQuery],
    queryFn: () => productionApi.realizedSummary(periodQuery),
    enabled: period != null,
  })
  const productsQuery = useQuery({
    queryKey: ['production', 'realized', 'products', periodQuery],
    queryFn: () => productionApi.realizedProducts(periodQuery),
    enabled: period != null,
  })
  const jobsQuery = useQuery({
    queryKey: ['production', 'realized', 'jobs'],
    queryFn: productionApi.realizedJobs,
  })
  const inventoryQuery = useQuery({
    queryKey: ['production', 'realized', 'inventory'],
    queryFn: productionApi.realizedInventory,
  })
  const warningsQuery = useQuery({
    queryKey: ['production', 'realized', 'warnings'],
    queryFn: productionApi.realizedWarnings,
  })
  const openingQuery = useQuery({
    queryKey: ['production', 'realized', 'opening'],
    queryFn: productionApi.realizedOpening,
  })
  const entriesQuery = useQuery({
    queryKey: ['production', 'realized', 'entries'],
    queryFn: productionApi.realizedEntries,
  })
  const archiveQuery = useQuery({
    queryKey: ['production', 'realized', 'archive'],
    queryFn: productionApi.realizedArchiveStatus,
  })
  const charactersQuery = useQuery({
    queryKey: ['characters', 'owners'],
    queryFn: charactersApi.owners,
  })

  useQueryError('Realized profit', summaryQuery.isError, summaryQuery.error)
  useQueryError('Realized products', productsQuery.isError, productsQuery.error)
  useQueryError('Realized jobs', jobsQuery.isError, jobsQuery.error)
  useQueryError('Realized inventory', inventoryQuery.isError, inventoryQuery.error)
  useQueryError('Realized warnings', warningsQuery.isError, warningsQuery.error)

  const job = useBackgroundJob({
    queryKey: ['production', 'realized', 'rebuild-status'],
    fetchStatus: productionApi.realizedRebuildStatus,
    resultKeys: [['production', 'realized']],
    labels: { rebuild_realized: 'Recompute realized profit' },
    defaultLabel: 'Recompute realized profit',
    pollIntervalMs: 1500,
    onSucceeded: () => setOffer(null),
  })
  const rebuild = useBackgroundJobStart(job, () => productionApi.startRealizedRebuild())
  const running = job.runningStatus || rebuild.isPending

  useEffect(() => {
    holdAutoRebuild()
    return () => scheduleAutoRebuildReset()
  }, [])

  useEffect(() => {
    const status = summaryQuery.data?.status
    if (!status || period == null) return
    if (!shouldAutoRecompute(status)) return
    if (!claimAutoRebuild()) return
    if (!running) rebuild.mutate()
  }, [summaryQuery.data, period, running, rebuild])

  function selectTab(value: string | null) {
    if (!value || !(REALIZED_TABS as readonly string[]).includes(value)) return
    const next = value as RealizedTabId
    setTab(next)
    setFocus(null)
    writeRealizedTab(next)
  }

  function openWarning(target: WarningFocus) {
    setTab(target.tab)
    writeRealizedTab(target.tab)
    setFocus(target.tab === 'jobs' ? { jobRef: target.jobRef } : { typeId: target.typeId })
  }

  function applyCustom() {
    const error = customRangeError(draftStart, draftEnd)
    if (error) {
      notify({ title: 'Custom range', message: error, color: 'danger' })
      return
    }
    setCustom({ start: draftStart, end: draftEnd })
  }

  async function previewFreight() {
    setFreightBusy(true)
    try {
      const preview = await productionApi.reapplyRealizedFreight(true)
      const count = preview.would_update ?? 0
      const purchases = count === 1 ? 'purchase' : 'purchases'
      modals.openConfirmModal({
        title: 'Re-apply current freight rate',
        children: (
          <Text size="sm">
            {count} {purchases} would change to {isk(preview.freight_rate_per_m3)}/m³ ({preview.freight_label}).
          </Text>
        ),
        labels: { confirm: 'Apply', cancel: 'Cancel' },
        onConfirm: () => { void applyFreight() },
      })
    } catch (error) {
      notify({
        title: 'Re-apply freight rate - Error',
        message: error instanceof ApiError ? error.message : String(error),
        color: 'danger',
      })
    } finally {
      setFreightBusy(false)
    }
  }

  async function applyFreight() {
    try {
      const result = await productionApi.reapplyRealizedFreight(false)
      const updated = result.updated ?? 0
      notify({ title: 'Re-apply freight rate', message: `Updated ${updated} purchases.`, color: 'accent' })
      if (result.rebuild_required) {
        setOffer(`Freight rate applied to ${updated} purchases. Recompute to include it in realized profit.`)
      }
    } catch (error) {
      notify({
        title: 'Re-apply freight rate - Error',
        message: error instanceof ApiError ? error.message : String(error),
        color: 'danger',
      })
    }
  }

  const summary = summaryQuery.data
  const hint = summary?.status.hint || REBUILD_HINT
  const typeFocus = focus && 'typeId' in focus ? focus.typeId : null
  const jobFocus = focus && 'jobRef' in focus ? focus.jobRef : null

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <Stack gap="xs">
          <SegmentedControl
            value={preset}
            onChange={(value) => setPreset(value as Preset)}
            data={PERIOD_OPTIONS}
          />
          {preset === 'custom' && (
            <Group align="flex-end">
              <TextInput label="Start" type="date" value={draftStart} onChange={(event) => setDraftStart(event.currentTarget.value)} />
              <TextInput label="End" type="date" value={draftEnd} onChange={(event) => setDraftEnd(event.currentTarget.value)} />
              <Button variant="default" onClick={applyCustom}>Apply range</Button>
            </Group>
          )}
        </Stack>
        <Tooltip label="Rebuild realized profit from the archive." multiline w={240}>
          <Button onClick={() => rebuild.mutate()} loading={running}>Recompute</Button>
        </Tooltip>
      </Group>

      {running && <JobProgress label={job.formatProgress(job.status?.progress, job.jobName)} progress={job.status?.progress} />}
      {summary && <Text size="sm" c="dimmed">{statusLine(summary)}</Text>}
      {offer && <HintCard>{offer}</HintCard>}
      {focus && (
        <Group gap="xs">
          <Badge variant="light" color="info">
            {typeFocus != null ? `Filtered to type ${typeFocus}` : `Filtered to job ${jobFocus}`}
          </Badge>
          <Button size="compact-xs" variant="subtle" onClick={() => setFocus(null)}>Clear filter</Button>
        </Group>
      )}

      <Tabs value={tab} onChange={selectTab}>
        <Tabs.List>
          <Tabs.Tab value="overview">Overview</Tabs.Tab>
          <Tabs.Tab value="products">Products</Tabs.Tab>
          <Tabs.Tab value="jobs">Jobs</Tabs.Tab>
          <Tabs.Tab value="inventory">Inventory</Tabs.Tab>
          <Tabs.Tab value="warnings">Warnings</Tabs.Tab>
          <Tabs.Tab value="ledger">Ledger</Tabs.Tab>
        </Tabs.List>
      </Tabs>

      {period == null && <HintCard>Choose a start and end date, then apply the range.</HintCard>}

      {tab === 'overview' && period != null && (
        summaryQuery.isLoading ? <Text size="sm" c="dimmed">Loading…</Text>
          : summaryQuery.isError ? <HintCard>Failed to load realized profit.</HintCard>
            : summary ? <OverviewTab summary={summary} period={period} />
              : null
      )}
      {tab === 'products' && (
        <ProductsTab
          rows={productsQuery.data?.rows ?? []}
          needsRebuild={Boolean(productsQuery.data?.needs_rebuild)}
          hint={hint}
          periodQuery={periodQuery}
          typeId={typeFocus}
          locationNames={locationNames}
          isLoading={productsQuery.isLoading}
          isError={productsQuery.isError}
          onRetry={() => { void productsQuery.refetch() }}
          updatedAt={productsQuery.dataUpdatedAt}
        />
      )}
      {tab === 'jobs' && (
        <JobsTab
          rows={jobsQuery.data?.rows ?? []}
          needsRebuild={Boolean(jobsQuery.data?.needs_rebuild)}
          hint={hint}
          jobRef={jobFocus}
          isLoading={jobsQuery.isLoading}
          isError={jobsQuery.isError}
          onRetry={() => { void jobsQuery.refetch() }}
          updatedAt={jobsQuery.dataUpdatedAt}
        />
      )}
      {tab === 'inventory' && (
        <InventoryTab
          rows={inventoryQuery.data?.rows ?? []}
          needsRebuild={Boolean(inventoryQuery.data?.needs_rebuild)}
          hint={hint}
          typeId={typeFocus}
          isLoading={inventoryQuery.isLoading}
          isError={inventoryQuery.isError}
          onRetry={() => { void inventoryQuery.refetch() }}
          updatedAt={inventoryQuery.dataUpdatedAt}
        />
      )}
      {tab === 'warnings' && (
        <WarningsTab
          groups={warningsQuery.data?.groups ?? []}
          needsRebuild={Boolean(warningsQuery.data?.needs_rebuild)}
          hint={hint}
          onOpen={openWarning}
        />
      )}
      {tab === 'ledger' && (
        <LedgerTab
          opening={openingQuery.data?.rows ?? []}
          openingNeedsRebuild={Boolean(openingQuery.data?.needs_rebuild)}
          hint={hint}
          entries={entriesQuery.data?.rows ?? []}
          archive={archiveQuery.data?.rows ?? []}
          characters={charactersQuery.data}
          onRebuildOffer={setOffer}
          freightBusy={freightBusy}
          onFreightPreview={() => { void previewFreight() }}
        />
      )}
    </Stack>
  )
}
