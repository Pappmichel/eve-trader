import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Accordion, Badge, Button, Group, Stack, Table, Text, Title, Tooltip,
} from '@mantine/core'
import { IconRefresh } from '@tabler/icons-react'
import { Link } from 'react-router-dom'

import { doctrineApi, sdeApi } from '../../api/client'
import type { ContractMarketFitting } from '../../api/types'
import { SearchableSelect } from '../../components/SearchableSelect'
import { HintCard } from '../../components/HintCard'
import { JobProgress } from '../../components/JobProgress'
import { useBackgroundJob, useBackgroundJobStart } from '../../hooks/useBackgroundJob'
import { dateTime, isk } from '../../format'

const QUERY_KEY = ['doctrine', 'contract-market']
const SCAN_RESULT_KEYS: string[][] = [QUERY_KEY, ['doctrine', 'settings']]

function SourceBadge({ source }: { source: 'public' | 'alliance' }) {
  return (
    <Badge size="sm" variant="light" color={source === 'public' ? 'blue' : 'violet'}>
      {source === 'public' ? 'Public' : 'Alliance'}
    </Badge>
  )
}

function FittingPanel({ fitting, scanned }: { fitting: ContractMarketFitting; scanned: boolean }) {
  if (fitting.count === 0) {
    return (
      <Stack gap="xs">
        {fitting.hull_name && (
          <Text size="sm" c="dimmed">{fitting.hull_name}</Text>
        )}
        <Text size="sm" c="dimmed">
          {scanned
            ? 'No contracts for this fitting in the last scan.'
            : 'Not scanned yet for this fitting.'}
        </Text>
      </Stack>
    )
  }
  return (
    <Stack gap="xs">
      <Group gap="md">
        <Text size="sm"><Text span c="dimmed">Count:</Text> {fitting.count}</Text>
        <Text size="sm"><Text span c="dimmed">Min:</Text> {isk(fitting.min_price)}</Text>
        <Text size="sm"><Text span c="dimmed">Avg:</Text> {isk(fitting.avg_price)}</Text>
        {fitting.hull_name && (
          <Text size="sm" c="dimmed">{fitting.hull_name}</Text>
        )}
      </Group>
      <Table.ScrollContainer minWidth={640}>
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Source</Table.Th>
              <Table.Th>Price</Table.Th>
              <Table.Th>Seller</Table.Th>
              <Table.Th>Title</Table.Th>
              <Table.Th>Location</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Expires</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {fitting.contracts.map((c) => (
              <Table.Tr key={c.contract_id}>
                <Table.Td><SourceBadge source={c.source} /></Table.Td>
                <Table.Td>{isk(c.price)}</Table.Td>
                <Table.Td>{c.issuer_name ?? (c.issuer_id != null ? String(c.issuer_id) : '–')}</Table.Td>
                <Table.Td>{c.title || '–'}</Table.Td>
                <Table.Td>{c.location_name ?? (c.start_location_id != null ? String(c.start_location_id) : '–')}</Table.Td>
                <Table.Td tt="capitalize">{c.validation_status}</Table.Td>
                <Table.Td>{dateTime(c.date_expired)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  )
}

function FittingCountBadge({
  count, minPrice, scanned,
}: { count: number; minPrice: number | null; scanned: boolean }) {
  if (!scanned) {
    return <Badge variant="light" color="gray">Not scanned</Badge>
  }
  if (count === 0) {
    return <Badge variant="light" color="red">0 contracts</Badge>
  }
  return <Text size="xs" c="dimmed">{count} · min {isk(minPrice)}</Text>
}

function DoctrineCountBadge({ total, scanned }: { total: number; scanned: boolean }) {
  if (!scanned) {
    return <Badge variant="light" color="gray">Not scanned</Badge>
  }
  if (total === 0) {
    return <Badge variant="light" color="red">0 contracts</Badge>
  }
  return <Badge variant="light">{total} contract{total === 1 ? '' : 's'}</Badge>
}

export default function ContractMarket() {
  const { data, isLoading, isError } = useQuery({ queryKey: QUERY_KEY, queryFn: doctrineApi.contractMarket })
  const { data: settings } = useQuery({ queryKey: ['doctrine', 'settings'], queryFn: doctrineApi.settings })
  const { data: solarSystems } = useQuery({
    queryKey: ['sde', 'solar-systems'],
    queryFn: sdeApi.solarSystems,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
  const systemOptions = useMemo(
    () => (solarSystems ?? []).map((s) => ({
      value: String(s.solar_system_id), label: s.solar_system_name,
    })),
    [solarSystems],
  )

  const [systemId, setSystemId] = useState<string | null>(null)
  useEffect(() => {
    const saved = data?.settings.system_id ?? settings?.contract_market_system_id
    if (saved != null) setSystemId(String(saved))
  }, [data?.settings.system_id, settings?.contract_market_system_id])

  const scanJob = useBackgroundJob({
    queryKey: ['doctrine', 'pipeline', 'contract-market'],
    fetchStatus: doctrineApi.scanContractMarketStatus,
    resultKeys: SCAN_RESULT_KEYS,
    labels: { contract_market_scan: 'Contract Market Scan' },
    defaultLabel: 'Contract Market Scan',
    pollIntervalMs: 1000,
  })
  const scanStart = useBackgroundJobStart(scanJob, () =>
    doctrineApi.scanContractMarket(systemId ? Number(systemId) : null))
  const scanning = scanJob.runningStatus || scanStart.isPending

  const includePublic = settings?.contract_market_include_public ?? true
  const includeAlliance = settings?.contract_market_include_alliance ?? true
  const nothingEnabled = !includePublic && !includeAlliance

  return (
    <Stack>
      <Title order={4}>Contract Market</Title>
      <HintCard>
        Scan public and alliance item-exchange contracts in one solar system and match them against
        your Doctrine fittings. Results are separate from your own Contracts tab (owner-issued snapshot).
        Alliance contracts come through a corporation that shares Contracts with Doctrine; ESI has no
        alliance endpoint, so this relies on undocumented behaviour. Public contracts need a place the
        tool already knows, such as a station or a structure one of your characters has resolved.
        Toggle Public / Alliance under{' '}
        <Text span component={Link} to="/doctrine/settings" c="accent">Settings</Text>.
      </HintCard>

      <Group align="flex-end" wrap="wrap">
        <SearchableSelect
          label="Solar system"
          placeholder="Pick a system"
          data={systemOptions}
          value={systemId}
          onChange={setSystemId}
          w={280}
        />
        <Tooltip
          label={nothingEnabled
            ? 'Enable Public and/or Alliance in Doctrine Settings first.'
            : !systemId ? 'Pick a solar system first.' : 'Scan public and alliance contracts in this system.'}
          multiline w={260}
        >
          <Button
            leftSection={<IconRefresh size={14} />}
            loading={scanning}
            disabled={!systemId || nothingEnabled}
            onClick={() => scanStart.mutate()}
          >
            Scan
          </Button>
        </Tooltip>
        <Group gap="xs">
          <Badge variant="outline" color={includePublic ? 'blue' : 'gray'}>
            Public {includePublic ? 'on' : 'off'}
          </Badge>
          <Badge variant="outline" color={includeAlliance ? 'violet' : 'gray'}>
            Alliance {includeAlliance ? 'on' : 'off'}
          </Badge>
        </Group>
      </Group>

      {scanning && (
        <JobProgress
          label={scanJob.formatProgress(scanJob.status?.progress, scanJob.jobName)}
          progress={scanJob.status?.progress}
        />
      )}

      {data?.scan && (
        <Text size="sm" c="dimmed">
          Last scan {dateTime(data.scan.scanned_at)} in {data.scan.system_name ?? data.scan.system_id}
          {' · '}{data.scan.matched} matched
          {' · '}{data.scan.public_candidates} public / {data.scan.alliance_candidates} alliance candidates
        </Text>
      )}
      {data?.scan && data.scan.unknown_locations > 0 && (
        <Text size="sm" c="dimmed">
          {data.scan.unknown_locations} public contracts in this region are at structures the tool cannot
          place, so they may be missing here.
        </Text>
      )}
      {data?.scan && data.scan.items_pending > 0 && (
        <Text size="sm" c="dimmed">
          {data.scan.items_pending} contracts were not read yet. Scan again to continue.
        </Text>
      )}
      {data?.scan && data.scan.items_failed > 0 && (
        <Text size="sm" c="dimmed">{data.scan.items_failed} contracts could not be read from ESI.</Text>
      )}
      {data?.scan && data.scan.asks_for_items > 0 && (
        <Text size="sm" c="dimmed">
          {data.scan.asks_for_items} contracts ask the buyer for items and are not listed.
        </Text>
      )}
      {data?.scan && data.scan.alliance_skipped && (
        <Text size="sm" c="dimmed">
          Alliance contracts were not read: no sharing corporation has an alliance and a member
          token with the corporation contracts scope. Fittings below only reflect public contracts.
        </Text>
      )}

      {isLoading && <Text size="sm" c="dimmed">Loading…</Text>}
      {isError && <Text size="sm" c="danger">Could not load the last scan.</Text>}

      {!isLoading && data && data.scan === null && data.doctrines.length > 0 && (
        <Text size="sm" c="dimmed">No scan yet. Pick a system and click Scan.</Text>
      )}
      {!isLoading && data && data.doctrines.length === 0 && (
        <Text size="sm" c="dimmed">No doctrines configured.</Text>
      )}

      {data && data.doctrines.length > 0 && (
        <Accordion multiple variant="separated">
          {data.doctrines.map((d) => {
            const total = d.fittings.reduce((n, f) => n + f.count, 0)
            const scanned = data.scan !== null
            return (
              <Accordion.Item key={d.doctrine_id ?? d.doctrine_name} value={d.doctrine_id ?? d.doctrine_name}>
                <Accordion.Control>
                  <Group justify="space-between" pr="md" wrap="nowrap">
                    <Text fw={600}>{d.doctrine_name}</Text>
                    <DoctrineCountBadge total={total} scanned={scanned} />
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  {d.fittings.length === 0 ? (
                    <Text size="sm" c="dimmed">No fittings in this doctrine.</Text>
                  ) : (
                    <Accordion multiple variant="contained">
                      {d.fittings.map((f) => (
                        <Accordion.Item key={f.fitting_id} value={f.fitting_id}>
                          <Accordion.Control>
                            <Group justify="space-between" pr="md" wrap="nowrap">
                              <Text size="sm">{f.fitting_name}</Text>
                              <Group gap="xs">
                                <FittingCountBadge
                                  count={f.count}
                                  minPrice={f.min_price}
                                  scanned={scanned}
                                />
                              </Group>
                            </Group>
                          </Accordion.Control>
                          <Accordion.Panel>
                            <FittingPanel fitting={f} scanned={scanned} />
                          </Accordion.Panel>
                        </Accordion.Item>
                      ))}
                    </Accordion>
                  )}
                </Accordion.Panel>
              </Accordion.Item>
            )
          })}
        </Accordion>
      )}
    </Stack>
  )
}
