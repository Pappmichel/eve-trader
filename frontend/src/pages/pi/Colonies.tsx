import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Alert, Badge, Button, Group, Stack, Table, Text, Title, Tooltip } from '@mantine/core'

import { ApiError, piApi } from '../../api/client'
import type { PiColoniesCharacter, PiColonyView } from '../../api/types'
import { HintCard } from '../../components/HintCard'
import { dateTime } from '../../format'
import { useAction } from '../../hooks/useAction'
import { notify } from '../../notify'
import { Loading, QueryError, num } from './common'
import type { EditorOpenState } from './Editor'

const STATE_LABEL: Record<PiColoniesCharacter['state'], string> = {
  ok: 'synced',
  not_shared: 'not shared',
  reauth_needed: 're-auth needed',
  not_synced: 'not synced',
}

function hoursLeft(h: number | null): string {
  if (h === null) return '–'
  if (h <= 0) return 'expired'
  return `${num(h, 1)} h`
}

function ColonyRow({ colony, opening, onOpen }: {
  colony: PiColonyView
  opening: boolean
  onOpen: () => void
}) {
  const p = colony.projection
  const extractors = p.extractors.map((e) => (
    `${e.product_name ?? 'P0'} · ${e.heads} heads · ${hoursLeft(e.hours_left)} · ${e.rate_source}`
  )).join('; ')
  return (
    <Table.Tr>
      <Table.Td>{colony.planet_name ?? colony.planet_id}</Table.Td>
      <Table.Td>{colony.planet_type ?? '–'}</Table.Td>
      <Table.Td>{colony.zone}</Table.Td>
      <Table.Td>{p.product_name ?? '–'}{p.chain ? ` (${p.chain})` : ''}</Table.Td>
      <Table.Td>{extractors || '–'}</Table.Td>
      <Table.Td>
        {p.idle_factories.length > 0 && <Text size="sm">{p.idle_factories.length} idle</Text>}
        {p.hours_until_full !== null && <Text size="sm">Full in {num(p.hours_until_full, 1)} h</Text>}
        {p.skipped_routes > 0 && <Text size="sm" c="warn">{p.skipped_routes} route(s) dropped</Text>}
        {p.cc_bypassed > 0 && <Text size="sm" c="dimmed">Command center bypassed on {p.cc_bypassed} route(s)</Text>}
        {p.uncertain && <Text size="sm" c="dimmed">Last touched {num(p.age_hours, 0)} h ago</Text>}
      </Table.Td>
      <Table.Td>
        <Button size="compact-xs" variant="default" disabled={!colony.template_available} loading={opening}
          onClick={onOpen}>
          Open in editor
        </Button>
      </Table.Td>
    </Table.Tr>
  )
}

export default function Colonies() {
  const navigate = useNavigate()
  const { data, isLoading, error } = useQuery({ queryKey: ['pi', 'colonies'], queryFn: piApi.colonies })
  const calibration = useQuery({ queryKey: ['pi', 'calibration'], queryFn: piApi.calibration })
  const sync = useAction('Sync colonies', () => piApi.syncColonies(), [['pi', 'colonies'], ['pi', 'calibration']], {
    tier: 'live',
    effect: 'Refreshes Planetary Industry colonies and skills from EVE.',
  })
  const [opening, setOpening] = useState<string | null>(null)

  const openColony = async (character: PiColoniesCharacter, colony: PiColonyView) => {
    const key = `${character.character_id}:${colony.planet_id}`
    setOpening(key)
    try {
      const res = await piApi.colonyTemplate(character.character_id, colony.planet_id)
      const state: EditorOpenState = {
        template: res.template,
        template_json: res.template_json,
        planet_id: colony.planet_id,
        radius_km: colony.radius_km ?? undefined,
        name: colony.planet_name ?? undefined,
        source: 'esi',
      }
      navigate('/pi/editor', { state })
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err)
      notify({ title: 'Open colony', message, color: 'danger' })
    } finally {
      setOpening(null)
    }
  }

  return (
    <Stack>
      <HintCard>
        Colonies synced from EVE. Extractor rates use each program&apos;s own cycle when ESI reports it;
        an expired program contributes nothing. Share Planetary Industry on the Characters page, then sync.
      </HintCard>
      <Alert color="gray" title="Pin positions are not checked in game">
        Exported latitude and longitude are used as the template&apos;s La/Lo. Confirm one colony against its
        in-game export before relying on the geometry.
      </Alert>
      <Group>
        <Tooltip label={sync.tooltip} disabled={!sync.tooltip} multiline w={280}>
          <Button variant="default" leftSection={sync.tierIcon} loading={sync.isPending} onClick={() => sync.mutate()}>
            Sync from EVE
          </Button>
        </Tooltip>
      </Group>
      {error ? <QueryError error={error} /> : null}
      {isLoading ? <Loading /> : (data?.characters ?? []).map((c) => (
        <Stack key={c.character_id} gap="xs">
          <Group gap="xs">
            <Title order={5}>{c.character_name}</Title>
            <Badge variant="light" color={c.state === 'ok' ? 'accent' : 'gray'}>{STATE_LABEL[c.state]}</Badge>
            {c.synced_at && <Text size="xs" c="dimmed">synced {dateTime(c.synced_at)}</Text>}
          </Group>
          {c.detail && <Text size="sm" c="dimmed">{c.detail}</Text>}
          {c.state === 'not_shared' && (
            <Text size="sm" c="dimmed">Share Planetary Industry with this tool on the Characters page.</Text>
          )}
          {c.colonies.length > 0 && (
            <Table withRowBorders={false} verticalSpacing={4}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Planet</Table.Th><Table.Th>Type</Table.Th><Table.Th>Zone</Table.Th>
                  <Table.Th>Product</Table.Th><Table.Th>Extractors</Table.Th><Table.Th>State</Table.Th><Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {c.colonies.map((colony) => (
                  <ColonyRow key={colony.planet_id} colony={colony}
                    opening={opening === `${c.character_id}:${colony.planet_id}`}
                    onOpen={() => openColony(c, colony)} />
                ))}
              </Table.Tbody>
            </Table>
          )}
          {c.state === 'ok' && c.colonies.length === 0 && <Text size="sm" c="dimmed">No colonies on this character.</Text>}
        </Stack>
      ))}
      {data && data.characters.length === 0 && <Text c="dimmed">No characters with a token.</Text>}

      {calibration.data && (
        <Stack gap="xs">
          <Title order={6} c="dimmed" tt="uppercase">Calibrated yields</Title>
          <Text size="xs" c="dimmed">
            {calibration.data.sample_count} extractor sample(s). A zone replaces the default yield once it has{' '}
            {calibration.data.min_samples} samples.
          </Text>
          <Table withRowBorders={false} verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Zone</Table.Th><Table.Th ta="right">Samples</Table.Th>
                <Table.Th ta="right">Median</Table.Th><Table.Th ta="right">Default</Table.Th><Table.Th>Active</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {calibration.data.zones.map((z) => (
                <Table.Tr key={z.zone}>
                  <Table.Td>{z.zone}</Table.Td>
                  <Table.Td ta="right">{z.count}</Table.Td>
                  <Table.Td ta="right">{z.median === null ? '–' : num(z.median, 0)}</Table.Td>
                  <Table.Td ta="right">{num(z.default, 0)}</Table.Td>
                  <Table.Td>{z.active ? 'yes' : 'no'}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Stack>
      )}
    </Stack>
  )
}
