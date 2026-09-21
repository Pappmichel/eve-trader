import { useMemo, useState, type ReactNode } from 'react'
import {
  Badge, Button, Container, Divider, Group, Popover, Stack, Switch, Table, Text, Title, Tooltip,
} from '@mantine/core'
import { modals } from '@mantine/modals'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useQueries, useQuery } from '@tanstack/react-query'

import { charactersApi } from '../../api/client'
import type {
  AccessPreview, CorporationRoleCheckResult, EsiCapabilityRow, EsiFreshnessRow, EsiSharingRow, EsiTokenCharacter,
} from '../../api/types'
import {
  ACCESS_CAPABILITIES,
  CHARACTER_KINDS,
  GROUP_1_KINDS,
  capabilityByKey,
  formatCorpRoles,
  kindByKey,
  toolLabel,
} from '../../esiRegistry'
import {
  buildToolView,
  characterRemovalImpact,
  corporationIdsFrom,
  deriveCellState,
  extraKindsForCharacter,
  pendingKeysFromPreview,
  type CellKind,
  type CellState,
} from '../../esiAccess'
import { useAction } from '../../hooks/useAction'
import { openAccessConfirmModal } from '../../roleAccessDescriptions'

const CHARACTERS_KEYS = [
  ['characters', 'owners'],
  ['characters', 'sharing'],
  ['characters', 'freshness'],
  ['characters', 'capabilities'],
  ['characters', 'access-preview'],
]

const DECISION_4 =
  'Sharing governs raw ESI snapshots only — assets, jobs, blueprints, orders, contracts, wallet, and skills. '
  + 'Derived tables (realized trades, shortlists, production plans) are not filtered by it. '
  + 'Unticking Wallet does not erase last week\'s realized trades.'

const CELL_LABEL: Record<CellKind, string> = {
  not_shared: 'not shared',
  all: 'all tools',
  some: 'some',
  pending: 're-auth',
  error: 'error',
}

function cellColor(kind: CellKind): string {
  if (kind === 'all') return 'accent'
  if (kind === 'some') return 'info'
  if (kind === 'pending') return 'warn'
  if (kind === 'error') return 'danger'
  return 'gray'
}

function cellText(state: CellState): string {
  if (state.kind === 'some') return `${state.sharedCount}/${state.capableCount}`
  return CELL_LABEL[state.kind]
}

function SharingCell({
  ownerType,
  ownerId,
  dataKind,
  sharing,
  freshness,
  pendingKinds,
  onToggle,
  pendingToggle,
}: {
  ownerType: string
  ownerId: number
  dataKind: string
  sharing: EsiSharingRow[]
  freshness: EsiFreshnessRow[]
  pendingKinds: ReadonlySet<string>
  onToggle: (toolKey: string, enabled: boolean) => void
  pendingToggle: string | null
}) {
  const [opened, setOpened] = useState(false)
  const kind = kindByKey(dataKind)
  const state = deriveCellState({
    ownerType, ownerId, dataKind, sharing, freshness, pendingKinds,
  })
  const shared = new Set(
    sharing
      .filter((r) => r.owner_type === ownerType && r.owner_id === ownerId && r.data_kind === dataKind)
      .map((r) => r.tool_key),
  )

  return (
    <Popover opened={opened} onChange={setOpened} position="right-start" withArrow shadow="md" width={260} withinPortal>
      <Popover.Target>
        <Button
          size="compact-xs"
          variant={state.kind === 'not_shared' ? 'subtle' : 'light'}
          color={cellColor(state.kind)}
          onClick={() => setOpened((o) => !o)}
          aria-label={`${dataKind} ${cellText(state)}`}
        >
          {cellText(state)}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Text size="sm" fw={600}>{kind?.label ?? dataKind}</Text>
          <Text size="xs" c="dimmed">Toggling writes or deletes one sharing row. It does not call ESI.</Text>
          {(kind?.consumingTools ?? []).map((toolKey) => (
            <Switch
              key={toolKey}
              size="sm"
              label={toolLabel(toolKey)}
              checked={shared.has(toolKey)}
              disabled={pendingToggle === `${ownerType}:${ownerId}:${dataKind}:${toolKey}`}
              onChange={(event) => onToggle(toolKey, event.currentTarget.checked)}
            />
          ))}
          {state.kind === 'pending' && (
            <Text size="xs" c="warn">Ticked, but this character still needs a re-authorize for the new scopes.</Text>
          )}
          {state.kind === 'error' && state.lastError && (
            <Text size="xs" c="danger">{state.lastError}</Text>
          )}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  )
}

function CharactersSection({
  owners,
  sharing,
  freshness,
  pendingByCharacter,
  onToggle,
  pendingToggle,
  onReauth,
  reauthPendingId,
  onRemove,
  removePendingId,
}: {
  owners: EsiTokenCharacter[]
  sharing: EsiSharingRow[]
  freshness: EsiFreshnessRow[]
  pendingByCharacter: Map<number, Set<string>>
  onToggle: (ownerType: string, ownerId: number, dataKind: string, toolKey: string, enabled: boolean) => void
  pendingToggle: string | null
  onReauth: (owner: EsiTokenCharacter) => void
  reauthPendingId: number | null
  onRemove: (owner: EsiTokenCharacter) => void
  removePendingId: number | null
}) {
  return (
    <div>
      <Title order={2} mb="xs">Characters</Title>
      <Text size="sm" c="dimmed" mb="sm">
        Rows are registered ESI characters. Click a cell to share that data kind with the tools that can consume it.
      </Text>
      {owners.length === 0 ? (
        <Text size="sm" c="dimmed">
          No ESI characters registered yet. Press &quot;Add character&quot; above and log in with EVE SSO —
          that first round is identity only. Tick the data kinds you want afterwards and press
          Re-authorize to grant their scopes. Configure sharing on this page; there is no per-tool
          character login any more.
        </Text>
      ) : (
        <div style={{ overflowX: 'auto' }}>
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Character</Table.Th>
              {CHARACTER_KINDS.map((k) => <Table.Th key={k.key}>{k.label}</Table.Th>)}
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {owners.map((owner) => {
              const pending = pendingByCharacter.get(owner.character_id) ?? new Set()
              return (
                <Table.Tr key={owner.character_id}>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Text size="sm" fw={600}>{owner.character_name || `#${owner.character_id}`}</Text>
                      {owner.character_has_token_pool && (
                        <Tooltip
                          multiline
                          w={280}
                          label="This character still has more than one token from the old per-tool logins. Re-authorize once to merge them."
                        >
                          <Badge size="xs" color="warn" variant="light">token pool</Badge>
                        </Tooltip>
                      )}
                      {pending.size > 0 && (
                        <Tooltip
                          multiline
                          w={280}
                          label={`Ticked but not yet on any token, needs Re-authorize: ${
                            [...pending].map((key) => kindByKey(key)?.label ?? key).join(', ')
                          }`}
                        >
                          <Badge size="xs" color="warn" variant="filled">re-auth needed</Badge>
                        </Tooltip>
                      )}
                    </Group>
                  </Table.Td>
                  {CHARACTER_KINDS.map((k) => (
                    <Table.Td key={k.key}>
                      <SharingCell
                        ownerType="character"
                        ownerId={owner.character_id}
                        dataKind={k.key}
                        sharing={sharing}
                        freshness={freshness}
                        pendingKinds={pending}
                        pendingToggle={pendingToggle}
                        onToggle={(toolKey, enabled) =>
                          onToggle('character', owner.character_id, k.key, toolKey, enabled)}
                      />
                    </Table.Td>
                  ))}
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Button
                        size="compact-xs"
                        variant="default"
                        loading={reauthPendingId === owner.character_id}
                        onClick={() => onReauth(owner)}
                      >
                        Re-authorize
                      </Button>
                      <Button
                        size="compact-xs"
                        variant="subtle"
                        color="danger"
                        loading={removePendingId === owner.character_id}
                        onClick={() => onRemove(owner)}
                      >
                        Remove
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              )
            })}
          </Table.Tbody>
        </Table>
        </div>
      )}
    </div>
  )
}

function RoleWarningBadge({
  corpId,
  dataKind,
  roleCheck,
}: {
  corpId: number
  dataKind: string
  roleCheck: CorporationRoleCheckResult | undefined
}) {
  const corp = roleCheck?.corporations.find((c) => c.corporation_id === corpId)
  const entry = corp?.data_kinds[dataKind]
  if (!entry || entry.has_role !== false) return null
  return (
    <Tooltip
      multiline
      w={260}
      label={`No checked character holds ${formatCorpRoles(entry.required_roles)} in this corp. `
        + (corp && corp.unchecked_characters.length > 0
          ? `Not checked: ${corp.unchecked_characters.join(', ')}.`
          : 'Tick Corporation roles for a member on the Access table to check more characters.')}
    >
      <Badge size="xs" color="danger" variant="light" mt={4}>role missing</Badge>
    </Tooltip>
  )
}

function CorporationsSection({
  corpIds,
  owners,
  sharing,
  freshness,
  onToggle,
  pendingToggle,
  roleCheck,
  onCheckRoles,
  checkRolesAction,
}: {
  corpIds: number[]
  owners: EsiTokenCharacter[]
  sharing: EsiSharingRow[]
  freshness: EsiFreshnessRow[]
  onToggle: (ownerType: string, ownerId: number, dataKind: string, toolKey: string, enabled: boolean) => void
  pendingToggle: string | null
  roleCheck: CorporationRoleCheckResult | undefined
  onCheckRoles: () => void
  checkRolesAction: { isPending: boolean; tooltip?: string; tierIcon?: ReactNode }
}) {
  const charactersByCorp = useMemo(() => {
    const map = new Map<number, string[]>()
    for (const owner of owners) {
      if (owner.corporation_id == null) continue
      const names = map.get(owner.corporation_id) ?? []
      names.push(owner.character_name || `#${owner.character_id}`)
      map.set(owner.corporation_id, names)
    }
    return map
  }, [owners])

  // Resolved corp name (best-effort, ESIClient.corporation_public_info) -
  // falls back to the raw ID below when the lookup failed for every owner
  // sharing that corp (rare: a public endpoint, only fails on ESI outage).
  const corpNameById = useMemo(() => {
    const map = new Map<number, string>()
    for (const owner of owners) {
      if (owner.corporation_id != null && owner.corporation_name) {
        map.set(owner.corporation_id, owner.corporation_name)
      }
    }
    return map
  }, [owners])

  return (
    <div>
      <Group justify="space-between" align="flex-start" mb="xs" wrap="nowrap">
        <Title order={2}>Corporations</Title>
        <Tooltip label={checkRolesAction.tooltip} disabled={!checkRolesAction.tooltip} multiline w={280}>
          <Button size="xs" variant="default" leftSection={checkRolesAction.tierIcon}
            onClick={onCheckRoles} loading={checkRolesAction.isPending}>
            Check corp roles
          </Button>
        </Tooltip>
      </Group>
      <Text size="sm" c="dimmed" mb="xs">
        Corp snapshots are reached through a registered member character, not a second login.
        EVE requires {formatCorpRoles(['Director'])} for assets, jobs, and blueprints;
        {' '}{formatCorpRoles(['Accountant', 'Trader'])} for market orders;
        {' '}{formatCorpRoles(['Accountant', 'Junior_Accountant'])} for wallet.
        A &quot;role missing&quot; badge below only appears for a corp with at least one member who has
        ticked Corporation roles (Access table) and re-authorized — otherwise a live ESI 403 stays
        the real check.
      </Text>
      {corpIds.length === 0 ? (
        <Text size="sm" c="dimmed">No corporation sharing or freshness rows yet.</Text>
      ) : (
        <div style={{ overflowX: 'auto' }}>
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Corporation</Table.Th>
              {GROUP_1_KINDS.map((k) => (
                <Table.Th key={k.key}>
                  <Stack gap={0}>
                    <Text span size="sm">{k.label}</Text>
                    {k.corpRoles.length > 0 && (
                      <Text span size="xs" c="dimmed">{formatCorpRoles(k.corpRoles)}</Text>
                    )}
                  </Stack>
                </Table.Th>
              ))}
              <Table.Th>Access via</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {corpIds.map((corpId) => {
              const accessVia = charactersByCorp.get(corpId) ?? []
              return (
              <Table.Tr key={corpId}>
                <Table.Td>
                  <Text size="sm" fw={600}>{corpNameById.get(corpId) ?? `Corporation ${corpId}`}</Text>
                </Table.Td>
                {GROUP_1_KINDS.map((k) => (
                  <Table.Td key={k.key}>
                    <SharingCell
                      ownerType="corporation"
                      ownerId={corpId}
                      dataKind={k.key}
                      sharing={sharing}
                      freshness={freshness}
                      pendingKinds={new Set()}
                      pendingToggle={pendingToggle}
                      onToggle={(toolKey, enabled) =>
                        onToggle('corporation', corpId, k.key, toolKey, enabled)}
                    />
                    <div>
                      <RoleWarningBadge corpId={corpId} dataKind={k.key} roleCheck={roleCheck} />
                    </div>
                  </Table.Td>
                ))}
                <Table.Td>
                  {accessVia.length > 0 ? (
                    <Text size="sm">{accessVia.join(', ')}</Text>
                  ) : (
                    <Text size="sm" c="dimmed">—</Text>
                  )}
                </Table.Td>
              </Table.Tr>
              )
            })}
          </Table.Tbody>
        </Table>
        </div>
      )}
    </div>
  )
}

function AccessSection({
  owners,
  capabilities,
  onToggle,
  pendingId,
}: {
  owners: EsiTokenCharacter[]
  capabilities: EsiCapabilityRow[]
  onToggle: (characterId: number, capabilityKey: string, enabled: boolean) => void
  pendingId: string | null
}) {
  const on = (characterId: number, key: string) =>
    capabilities.some((c) => c.character_id === characterId && c.capability_key === key)

  return (
    <div>
      <Title order={2} mb="xs">Access</Title>
      <Text size="sm" c="dimmed" mb="sm">
        On or off per character. No freshness, no scheduling, no per-tool sharing.
        Structure name resolution also needs Station Manager in-game for the corp variant.
      </Text>
      {owners.length === 0 ? (
        <Text size="sm" c="dimmed">No characters to grant access capabilities.</Text>
      ) : (
        <div style={{ overflowX: 'auto' }}>
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Capability</Table.Th>
              {owners.map((o) => (
                <Table.Th key={o.character_id}>{o.character_name || `#${o.character_id}`}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {ACCESS_CAPABILITIES.map((cap) => (
              <Table.Tr key={cap.key}>
                <Table.Td>
                  <Text size="sm">{cap.label}</Text>
                </Table.Td>
                {owners.map((o) => (
                  <Table.Td key={o.character_id}>
                    <Switch
                      size="sm"
                      aria-label={`${cap.label} ${o.character_name || o.character_id}`}
                      checked={on(o.character_id, cap.key)}
                      disabled={pendingId === `${o.character_id}:${cap.key}`}
                      onChange={(event) =>
                        onToggle(o.character_id, cap.key, event.currentTarget.checked)}
                    />
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        </div>
      )}
    </div>
  )
}

function ToolViewSection({
  sharing,
  owners,
}: {
  sharing: EsiSharingRow[]
  owners: EsiTokenCharacter[]
}) {
  const nameById = new Map(owners.map((o) => [o.character_id, o.character_name || `#${o.character_id}`]))
  const rows = buildToolView(sharing, (ownerType, ownerId) => {
    if (ownerType === 'character') return nameById.get(ownerId) ?? `character ${ownerId}`
    return `corporation ${ownerId}`
  })

  return (
    <div>
      <Title order={2} mb="xs">Tool view</Title>
      <Text size="sm" c="dimmed" mb="sm">
        Read-only. What each tool currently receives, and what has no source.
        This is how a tenant notices &quot;Sorting has no Assets source&quot; after the conservative migration.
      </Text>
      <Stack gap="md">
        {rows.map((row) => (
          <div key={row.toolKey}>
            <Title order={4}>{row.toolLabel}</Title>
            {row.receives.length === 0 && row.missing.length === 0 && (
              <Text size="sm" c="dimmed">This tool does not consume raw ESI snapshots.</Text>
            )}
            {row.receives.map((kind) => (
              <Text key={kind.kindKey} size="sm">
                {kind.kindLabel}: {kind.owners.map((o) => o.label).join(', ')}
              </Text>
            ))}
            {row.missing.map((kind) => (
              <Text key={kind.kindKey} size="sm" c="warn">
                {row.toolLabel} has no {kind.kindLabel} source
              </Text>
            ))}
          </div>
        ))}
      </Stack>
    </div>
  )
}

export default function CharactersPage() {
  const ownersQuery = useQuery({ queryKey: ['characters', 'owners'], queryFn: charactersApi.owners })
  const sharingQuery = useQuery({ queryKey: ['characters', 'sharing'], queryFn: () => charactersApi.sharing() })
  const freshnessQuery = useQuery({ queryKey: ['characters', 'freshness'], queryFn: charactersApi.freshness })
  const capabilitiesQuery = useQuery({
    queryKey: ['characters', 'capabilities'], queryFn: charactersApi.capabilities,
  })

  const owners = ownersQuery.data ?? []
  const sharing = sharingQuery.data ?? []
  const freshness = freshnessQuery.data ?? []
  const capabilities = capabilitiesQuery.data ?? []

  const extraByCharacter = useMemo(() => {
    const map = new Map<number, string[]>()
    for (const owner of owners) {
      map.set(owner.character_id, extraKindsForCharacter(owner.character_id, sharing, capabilities))
    }
    return map
  }, [owners, sharing, capabilities])

  const previewQueries = useQueries({
    queries: owners.map((owner) => {
      const extra = extraByCharacter.get(owner.character_id) ?? []
      return {
        queryKey: ['characters', 'access-preview', owner.character_id, extra.join(',')],
        queryFn: () => charactersApi.accessPreview(owner.character_id, extra),
        enabled: extra.length > 0,
      }
    }),
  })

  const pendingByCharacter = useMemo(() => {
    const map = new Map<number, Set<string>>()
    owners.forEach((owner, index) => {
      map.set(owner.character_id, pendingKeysFromPreview(previewQueries[index]?.data as AccessPreview | undefined))
    })
    return map
  }, [owners, previewQueries])

  const [pendingToggle, setPendingToggle] = useState<string | null>(null)
  const [pendingCapability, setPendingCapability] = useState<string | null>(null)
  const setSharing = useAction(
    'Update sharing',
    charactersApi.setSharing,
    CHARACTERS_KEYS,
  )
  const setCapability = useAction(
    'Update capability',
    charactersApi.setCapability,
    CHARACTERS_KEYS,
  )
  const syncAll = useAction(
    'Sync everything',
    () => charactersApi.sync(),
    CHARACTERS_KEYS,
    { tier: 'live', effect: 'Refreshes every owned data kind for every owner this tenant has shared.' },
  )
  const [roleCheck, setRoleCheck] = useState<CorporationRoleCheckResult | undefined>(undefined)
  const checkCorporationRoles = useAction(
    'Check corp roles',
    async () => {
      const result = await charactersApi.checkCorporationRoles()
      setRoleCheck(result)
      return result
    },
    [],
    {
      tier: 'live',
      effect: 'Live-fetches roles for every character with Corporation roles ticked (Access table) and re-authorized.',
    },
  )
  const addCharacter = useAction(
    'Add character',
    async () => {
      const { url } = await charactersApi.addStart()
      window.location.href = url
    },
  )
  const [reauthPendingId, setReauthPendingId] = useState<number | null>(null)
  const reauth = useAction(
    'Re-authorize',
    async (args: { characterId: number; extraKinds: string[] }) => {
      setReauthPendingId(args.characterId)
      try {
        const { url } = await charactersApi.reauthStart(args.characterId, args.extraKinds)
        window.location.href = url
      } finally {
        setReauthPendingId(null)
      }
    },
  )
  const [removePendingId, setRemovePendingId] = useState<number | null>(null)
  const removeCharacter = useAction(
    'Remove character',
    charactersApi.removeCharacter,
    CHARACTERS_KEYS,
  )

  const corpIds = corporationIdsFrom(sharing, freshness)

  const toggleSharing = (ownerType: string, ownerId: number, dataKind: string, toolKey: string, enabled: boolean) => {
    setPendingToggle(`${ownerType}:${ownerId}:${dataKind}:${toolKey}`)
    setSharing.mutate(
      { owner_type: ownerType, owner_id: ownerId, data_kind: dataKind, tool_key: toolKey, enabled },
      { onSettled: () => setPendingToggle(null) },
    )
  }

  const toggleCapability = (characterId: number, capabilityKey: string, enabled: boolean) => {
    setPendingCapability(`${characterId}:${capabilityKey}`)
    setCapability.mutate(
      { character_id: characterId, capability_key: capabilityKey, enabled },
      { onSettled: () => setPendingCapability(null) },
    )
  }

  const startReauth = async (owner: EsiTokenCharacter) => {
    const extra = extraByCharacter.get(owner.character_id) ?? []
    const preview = extra.length > 0
      ? await charactersApi.accessPreview(owner.character_id, extra)
      : { title: 'Confirm ESI access', items: [] as AccessPreview['items'] }
    openAccessConfirmModal(preview, () => {
      reauth.mutate({ characterId: owner.character_id, extraKinds: extra })
    })
  }

  const startRemove = (owner: EsiTokenCharacter) => {
    const impact = characterRemovalImpact(owner.character_id, sharing, capabilities)
    const name = owner.character_name || `#${owner.character_id}`
    const toolNames = impact.tools.length > 0
      ? impact.tools.map((key) => toolLabel(key)).join(', ')
      : 'not currently shared with any tool'
    const capNames = impact.capabilityKeys.length > 0
      ? impact.capabilityKeys.map((key) => capabilityByKey(key)?.label ?? key).join(', ')
      : 'no Access capabilities ticked'
    modals.openConfirmModal({
      title: 'Remove character',
      children: (
        <Stack gap="xs">
          <Text size="sm">
            Remove {name}? This deletes every ESI token for this character.
            They disappear from this page until you add them again.
          </Text>
          <Text size="sm">Shared with: {toolNames}</Text>
          <Text size="sm">Access: {capNames}</Text>
          <Text size="sm" c="dimmed">
            Sharing ticks and Access capabilities stay, so adding this character again
            restores them without re-ticking. Last-synced snapshots stay.
            This is not a per-tool unshare.
          </Text>
        </Stack>
      ),
      labels: { confirm: 'Remove', cancel: 'Cancel' },
      confirmProps: { color: 'danger' },
      onConfirm: () => {
        setRemovePendingId(owner.character_id)
        removeCharacter.mutate(owner.character_id, { onSettled: () => setRemovePendingId(null) })
      },
    })
  }

  return (
    <Container size="xl" py="xl">
      <Group justify="space-between" mb="lg" wrap="nowrap">
        <div>
          <Text tt="uppercase" size="xs" c="dimmed" fw={600} lts={2}>ESI access</Text>
          <Title order={1}>Characters</Title>
        </div>
        <Group gap="xs">
          <Tooltip
            multiline
            w={280}
            label="Logs a new character in with EVE SSO. The first round asks for no scopes at all — tick their data kinds here afterwards and press Re-authorize to grant them."
          >
            <Button size="xs" onClick={() => addCharacter.mutate()} loading={addCharacter.isPending}>
              Add character
            </Button>
          </Tooltip>
          <Tooltip label={syncAll.tooltip} disabled={!syncAll.tooltip} multiline w={280}>
            <Button size="xs" variant="default" onClick={() => syncAll.mutate()} loading={syncAll.isPending}>
              Sync everything
            </Button>
          </Tooltip>
          <Button component={Link} to="/" variant="subtle" leftSection={<IconArrowLeft size={14} />}>Back</Button>
        </Group>
      </Group>

      <Text size="sm" mb="xl">{DECISION_4}</Text>

      <Stack gap="xl">
        <CharactersSection
          owners={owners}
          sharing={sharing}
          freshness={freshness}
          pendingByCharacter={pendingByCharacter}
          onToggle={toggleSharing}
          pendingToggle={pendingToggle}
          onReauth={startReauth}
          reauthPendingId={reauthPendingId}
          onRemove={startRemove}
          removePendingId={removePendingId}
        />
        <Divider />
        <CorporationsSection
          corpIds={corpIds}
          owners={owners}
          sharing={sharing}
          freshness={freshness}
          onToggle={toggleSharing}
          pendingToggle={pendingToggle}
          roleCheck={roleCheck}
          onCheckRoles={() => checkCorporationRoles.mutate()}
          checkRolesAction={checkCorporationRoles}
        />
        <Divider />
        <AccessSection
          owners={owners}
          capabilities={capabilities}
          onToggle={toggleCapability}
          pendingId={pendingCapability}
        />
        <Divider />
        <ToolViewSection sharing={sharing} owners={owners} />
      </Stack>
    </Container>
  )
}
