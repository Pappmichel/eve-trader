import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Accordion, Badge, Button, Group, Stack, Table, Text, Title, Tooltip,
} from '@mantine/core'
import { IconRefresh } from '@tabler/icons-react'
import { Link } from 'react-router-dom'

import { doctrineApi } from '../../api/client'
import type { ContractMarketFitting } from '../../api/types'
import { SearchableSelect } from '../../components/SearchableSelect'
import { HintCard } from '../../components/HintCard'
import { JobProgress } from '../../components/JobProgress'
import { useBackgroundJob, useBackgroundJobStart } from '../../hooks/useBackgroundJob'
import { useSolarSystemOptions } from '../../hooks/useStaticOptions'
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

function FittingPanel({ fitting }: { fitting: ContractMarketFitting }) {
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
                <Table.Td>{c.validation_status}</Table.Td>
                <Table.Td>{dateTime(c.date_expired)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  )
}

export default function ContractMarket() {
  const { data, isLoading } = useQuery({ queryKey: QUERY_KEY, queryFn: doctrineApi.contractMarket })
  const { data: settings } = useQuery({ queryKey: ['doctrine', 'settings'], queryFn: doctrineApi.settings })
  const { data: solarSystems } = useSolarSystemOptions()
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

      {isLoading && <Text size="sm" c="dimmed">Loading…</Text>}

      {!isLoading && data && data.doctrines.length === 0 && (
        <Text size="sm" c="dimmed">
          {data.scan
            ? 'No Doctrine matches in that system for the last scan.'
            : 'No scan yet. Pick a system and click Scan.'}
        </Text>
      )}

      {data && data.doctrines.length > 0 && (
        <Accordion multiple variant="separated">
          {data.doctrines.map((d) => {
            const total = d.fittings.reduce((n, f) => n + f.count, 0)
            return (
              <Accordion.Item key={d.doctrine_id ?? d.doctrine_name} value={d.doctrine_id ?? d.doctrine_name}>
                <Accordion.Control>
                  <Group justify="space-between" pr="md" wrap="nowrap">
                    <Text fw={600}>{d.doctrine_name}</Text>
                    <Badge variant="light">{total} contract{total === 1 ? '' : 's'}</Badge>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <Accordion multiple variant="contained">
                    {d.fittings.map((f) => (
                      <Accordion.Item key={f.fitting_id} value={f.fitting_id}>
                        <Accordion.Control>
                          <Group justify="space-between" pr="md" wrap="nowrap">
                            <Text size="sm">{f.fitting_name}</Text>
                            <Group gap="xs">
                              <Text size="xs" c="dimmed">{f.count} · min {isk(f.min_price)}</Text>
                            </Group>
                          </Group>
                        </Accordion.Control>
                        <Accordion.Panel>
                          <FittingPanel fitting={f} />
                        </Accordion.Panel>
                      </Accordion.Item>
                    ))}
                  </Accordion>
                </Accordion.Panel>
              </Accordion.Item>
            )
          })}
        </Accordion>
      )}
    </Stack>
  )
}
