import { useState } from 'react'
import {
  Accordion, Alert, Badge, Button, Container, Group, Loader, NumberInput, Select, SimpleGrid, Stack, Table, Tabs,
  Text, TextInput, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconArrowLeft } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, charSkillsApi, gateApi } from '../../../api/client'
import type {
  CharacterSkills, SkillAttributes, SkillGroup, SkillMatrix, SkillQueue, SkillsOverviewRow, SkillsSummary,
} from '../../../api/types'
import { FieldState } from '../../../components/FieldState'
import { dateTime, duration, qty } from '../../../format'
import { useCharacterSync } from '../../../hooks/useCharacterSync'
import { isStale, newestStamp, useSyncWhenStale } from '../../../hooks/useSyncWhenStale'
import { DoctrineCheckTab } from './DoctrineCheckTab'
import { warningText } from './queueWarning'

const ROMAN = ['0', 'I', 'II', 'III', 'IV', 'V']

const EXTRACTABLE_HINT =
  'Upper bound: SP above the 5,000,000 SP floor, in steps of 500,000. The game also limits what can be '
  + 'pulled out of individual skills, so the real number can be lower.'

function level(n: number): string {
  return ROMAN[n] ?? String(n)
}

function untilSeconds(iso: string | null): number | null {
  if (!iso) return null
  const hasOffset = /[Zz]$|[+-]\d\d:\d\d$/.test(iso)
  return (new Date(hasOffset ? iso : `${iso}Z`).getTime() - Date.now()) / 1000
}

function Attributes({ attrs }: { attrs: SkillAttributes | null }) {
  if (!attrs) return <Text size="xs" c="dimmed">–</Text>
  return (
    <Text size="xs" ff="monospace">
      INT {attrs.intelligence} · MEM {attrs.memory} · PER {attrs.perception} · WIL {attrs.willpower} · CHA {attrs.charisma}
    </Text>
  )
}

function QueueSummary({ queue }: { queue: SkillQueue }) {
  if (queue.empty || queue.paused) {
    return queue.warning
      ? <Badge size="sm" color="warn" variant="light">{warningText(queue.warning)}</Badge>
      : <Text size="xs" c="dimmed">{queue.empty ? 'queue empty' : 'queue paused'}</Text>
  }
  const remaining = untilSeconds(queue.current?.finish_date ?? null)
  return (
    <div>
      {queue.warning && <Badge size="xs" color="warn" variant="light" mb={2}>{warningText(queue.warning)}</Badge>}
      <Text size="sm">
        {queue.current ? `${queue.current.name} ${level(queue.current.finished_level)}` : 'Nothing training'}
        {remaining !== null && remaining > 0 ? ` · ${duration(remaining)}` : ''}
      </Text>
      <Text size="xs" c="dimmed">
        {queue.length} in queue{queue.ends_at ? ` · ends ${dateTime(queue.ends_at)}` : ''}
      </Text>
    </div>
  )
}

function OverviewTab({ rows, onOpen }: { rows: SkillsOverviewRow[]; onOpen: (characterId: number) => void }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <Table striped highlightOnHover withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Character</Table.Th>
            <Table.Th ta="right">Total SP</Table.Th>
            <Table.Th ta="right">Unallocated</Table.Th>
            <Table.Th ta="right">
              <Tooltip label={EXTRACTABLE_HINT} multiline w={280}><span>Extractable (est.)</span></Tooltip>
            </Table.Th>
            <Table.Th>Attributes</Table.Th>
            <Table.Th>Skill queue</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((c) => (
            <Table.Tr key={c.character_id}>
              <Table.Td><Text size="sm" fw={600}>{c.character_name}</Text></Table.Td>
              <Table.Td ta="right">
                <FieldState field={c.summary}>{(s: SkillsSummary) => (s.total_sp === null ? '–' : qty(s.total_sp))}</FieldState>
              </Table.Td>
              <Table.Td ta="right">
                <FieldState field={c.summary}>{(s: SkillsSummary) => (s.unallocated_sp === null ? '–' : qty(s.unallocated_sp))}</FieldState>
              </Table.Td>
              <Table.Td ta="right">
                <FieldState field={c.summary}>
                  {(s: SkillsSummary) => (s.extractable_estimate === null ? '–' : `${s.extractable_estimate}×`)}
                </FieldState>
              </Table.Td>
              <Table.Td>
                <FieldState field={c.summary}>{(s: SkillsSummary) => <Attributes attrs={s.attributes} />}</FieldState>
              </Table.Td>
              <Table.Td><FieldState field={c.queue}>{(q: SkillQueue) => <QueueSummary queue={q} />}</FieldState></Table.Td>
              <Table.Td>
                <Button size="compact-xs" variant="default" onClick={() => onOpen(c.character_id)}>Skills</Button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </div>
  )
}

function GroupTable({ group }: { group: SkillGroup }) {
  return (
    <Table.ScrollContainer minWidth={600}>
      <Table withTableBorder striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Skill</Table.Th>
            <Table.Th ta="center">Level</Table.Th>
            <Table.Th ta="right">Rank</Table.Th>
            <Table.Th ta="right">SP</Table.Th>
            <Table.Th ta="right">SP to V</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {group.skills.map((s) => (
            <Table.Tr key={s.skill_id}>
              <Table.Td>{s.name}</Table.Td>
              <Table.Td ta="center">
                <Badge size="sm" variant="light" color={s.trained_level >= 5 ? 'accent' : 'gray'}>
                  {level(s.trained_level)}
                </Badge>
                {s.active_level !== s.trained_level && (
                  <Tooltip label={`Active level ${level(s.active_level)} (trained ${level(s.trained_level)})`}>
                    <Text span size="xs" c="dimmed"> ({level(s.active_level)})</Text>
                  </Tooltip>
                )}
              </Table.Td>
              <Table.Td ta="right">{s.rank === null ? '–' : s.rank}</Table.Td>
              <Table.Td ta="right">{qty(s.skillpoints)}</Table.Td>
              <Table.Td ta="right">{s.sp_to_level_v === null ? '–' : qty(s.sp_to_level_v)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  )
}

function QueueTable({ queue }: { queue: SkillQueue }) {
  if (queue.empty) {
    return <Text size="sm" c={queue.warning ? 'warn' : 'dimmed'}>The skill queue is empty.</Text>
  }
  return (
    <Stack gap={4}>
      {queue.warning && <Text size="sm" c="warn">{warningText(queue.warning)}</Text>}
      {queue.paused && <Text size="sm" c="warn">The queue is paused - ESI reports no finish dates.</Text>}
      <Table.ScrollContainer minWidth={0}>
        <Table withTableBorder striped>
          <Table.Tbody>
            {queue.entries.map((e) => (
              <Table.Tr key={e.queue_position}>
                <Table.Td>{e.queue_position + 1}</Table.Td>
                <Table.Td>{e.name} {level(e.finished_level)}</Table.Td>
                <Table.Td ta="right">{e.finish_date ? dateTime(e.finish_date) : '–'}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  )
}

// The queue guard: which queues need attention, and the threshold that decides
// "ends soon". 0 turns every warning off. Stored per tenant on the server.
function QueueGuard() {
  const queryClient = useQueryClient()
  const warnings = useQuery({ queryKey: ['char-skills', 'warnings'], queryFn: charSkillsApi.warnings })
  const [hours, setHours] = useState<number | string>('')
  const save = useMutation({
    mutationFn: (h: number) => charSkillsApi.setSettings(h),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['char-skills'] }),
    onError: (err: unknown) => notifications.show({
      title: 'Could not save', color: 'danger', message: err instanceof ApiError ? err.message : String(err),
    }),
  })
  const current = warnings.data?.queue_warning_hours
  const value = hours === '' ? (current ?? 24) : hours
  return (
    <Stack gap="xs" mb="md">
      {(warnings.data?.count ?? 0) > 0 && (
        <Alert color="warn" variant="light" title="Skill queues need attention">
          {warnings.data?.characters.map((c) => (
            <Text key={c.character_id} size="sm">{c.character_name}: {warningText(c)}</Text>
          ))}
        </Alert>
      )}
      <Group gap="xs" align="flex-end">
        <NumberInput
          label="Warn when a queue ends within (hours)"
          description="0 turns all queue warnings off"
          min={0} max={1440} w={260} value={value} onChange={setHours}
        />
        <Button
          size="xs" variant="default" loading={save.isPending}
          disabled={typeof value !== 'number' || value === current}
          onClick={() => save.mutate(Number(value))}
        >
          Save
        </Button>
      </Group>
    </Stack>
  )
}

function CharacterTab({ characters, selected, onSelect }: {
  characters: SkillsOverviewRow[]
  selected: number | null
  onSelect: (id: number | null) => void
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['char-skills', 'character', selected],
    queryFn: () => charSkillsApi.character(selected as number),
    enabled: selected !== null,
  })
  return (
    <Stack gap="md">
      <Select
        label="Character"
        placeholder="Pick a character"
        data={characters.map((c) => ({ value: String(c.character_id), label: c.character_name }))}
        value={selected === null ? null : String(selected)}
        onChange={(v) => onSelect(v === null ? null : Number(v))}
        w={280}
      />
      {selected === null ? <Text c="dimmed">Pick a character to see their skills.</Text>
        : isLoading ? <Loader color="accent" />
          : error || !data ? <Text c="danger">Could not load this character.</Text>
            : <CharacterDetail data={data} />}
    </Stack>
  )
}

function CharacterDetail({ data }: { data: CharacterSkills }) {
  return (
    <Stack gap="lg">
      <FieldState field={data.summary}>
        {(s: SkillsSummary) => (
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <div><Text size="xs" c="dimmed">Total SP</Text><Text fw={600}>{s.total_sp === null ? '–' : qty(s.total_sp)}</Text></div>
            <div><Text size="xs" c="dimmed">Unallocated SP</Text><Text fw={600}>{s.unallocated_sp === null ? '–' : qty(s.unallocated_sp)}</Text></div>
            <div>
              <Tooltip label={EXTRACTABLE_HINT} multiline w={280}><Text size="xs" c="dimmed">Extractable (est.)</Text></Tooltip>
              <Text fw={600}>{s.extractable_estimate === null ? '–' : `${s.extractable_estimate}×`}</Text>
            </div>
            <div><Text size="xs" c="dimmed">Attributes</Text><Attributes attrs={s.attributes} /></div>
          </SimpleGrid>
        )}
      </FieldState>

      <div>
        <Title order={4} mb={4}>Skill queue</Title>
        <FieldState field={data.queue}>{(q: SkillQueue) => <QueueTable queue={q} />}</FieldState>
      </div>

      <div>
        <Title order={4} mb={4}>Skills</Title>
        <FieldState field={data.skills}>
          {(groups: SkillGroup[]) => (
            groups.length === 0 ? <Text size="sm" c="dimmed">No skills.</Text> : (
              <Accordion multiple variant="separated">
                {groups.map((g) => (
                  <Accordion.Item key={g.group_name} value={g.group_name}>
                    <Accordion.Control>
                      <Group justify="space-between" pr="md">
                        <Text fw={600}>{g.group_name}</Text>
                        <Text size="xs" c="dimmed">
                          {g.skills.length} skills · {g.maxed} at V · {qty(g.total_sp)} SP
                        </Text>
                      </Group>
                    </Accordion.Control>
                    <Accordion.Panel><GroupTable group={g} /></Accordion.Panel>
                  </Accordion.Item>
                ))}
              </Accordion>
            )
          )}
        </FieldState>
      </div>
    </Stack>
  )
}

function MatrixTab() {
  const { data, isLoading } = useQuery({ queryKey: ['char-skills', 'matrix'], queryFn: charSkillsApi.matrix })
  const [filter, setFilter] = useState('')
  if (isLoading || !data) return <Loader color="accent" />
  return <MatrixTable matrix={data} filter={filter} onFilter={setFilter} />
}

function MatrixTable({ matrix, filter, onFilter }: {
  matrix: SkillMatrix
  filter: string
  onFilter: (v: string) => void
}) {
  const needle = filter.trim().toLowerCase()
  const groups = matrix.groups
    .map((g) => ({ ...g, skills: g.skills.filter((s) => !needle || s.name.toLowerCase().includes(needle)) }))
    .filter((g) => g.skills.length > 0)
  return (
    <Stack gap="sm">
      {matrix.characters.length === 0 ? (
        <Text c="dimmed">No character shares Skills yet. Tick it on the Characters page.</Text>
      ) : (
        <>
          <TextInput placeholder="Filter skills" value={filter} onChange={(e) => onFilter(e.currentTarget.value)} w={280} />
          {matrix.hidden_characters.length > 0 && (
            <Text size="xs" c="dimmed">
              Not shared, so no column: {matrix.hidden_characters.map((c) => c.character_name).join(', ')}.
            </Text>
          )}
          {matrix.reauth_needed.length > 0 && (
            <Text size="xs" c="warn">
              Shared but needs a re-authorize on the Characters page:{' '}
              {matrix.characters.filter((c) => matrix.reauth_needed.includes(c.character_id)).map((c) => c.character_name).join(', ')}.
            </Text>
          )}
          <Accordion multiple variant="separated">
            {groups.map((g) => (
              <Accordion.Item key={g.group_name} value={g.group_name}>
                <Accordion.Control>{g.group_name} <Text span size="xs" c="dimmed">({g.skills.length})</Text></Accordion.Control>
                <Accordion.Panel>
                  <div style={{ overflowX: 'auto' }}>
                    <Table withTableBorder striped>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>Skill</Table.Th>
                          {matrix.characters.map((c) => <Table.Th key={c.character_id} ta="center">{c.character_name}</Table.Th>)}
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {g.skills.map((s) => (
                          <Table.Tr key={s.skill_id}>
                            <Table.Td>{s.name}</Table.Td>
                            {matrix.characters.map((c) => {
                              const l = s.levels[String(c.character_id)]
                              return (
                                <Table.Td key={c.character_id} ta="center">
                                  {l ? level(l.trained) : <Text span c="dimmed">–</Text>}
                                </Table.Td>
                              )
                            })}
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  </div>
                </Accordion.Panel>
              </Accordion.Item>
            ))}
          </Accordion>
        </>
      )}
    </Stack>
  )
}

const QUEUE_KINDS = ['skillqueue']
const AUTO_SYNC_KEYS = [['char-skills']]

export default function SkillsPage() {
  const [tab, setTab] = useState<string | null>('overview')
  const [selected, setSelected] = useState<number | null>(null)
  const overview = useQuery({ queryKey: ['char-skills', 'overview'], queryFn: charSkillsApi.overview })
  const characters = overview.data?.characters ?? []
  const gateStatus = useQuery({ queryKey: ['gate', 'status'], queryFn: gateApi.status })
  const tools = gateStatus.data?.tools
  // The check reads Doctrine's fittings, so it needs that grant as well.
  const canDoctrine = tools === undefined || tools.includes('doctrine')

  const refresh = useCharacterSync(
    'Skills refresh',
    charSkillsApi.sync,
    [['char-skills']],
    'Syncs skills, attributes, SP totals and the skill queue for every character shared with Skills.',
  )

  // The skill queue is no longer refreshed in the background (esi_data
  // `on_demand`), so opening the page with an old queue syncs it once.
  useSyncWhenStale({
    ready: overview.isSuccess,
    stale: isStale(characters.map((c) => newestStamp(QUEUE_KINDS.map((k) => c.freshness[k]?.last_attempt_at)))),
    sync: charSkillsApi.sync,
    invalidateKeys: AUTO_SYNC_KEYS,
  })

  const openCharacter = (characterId: number) => {
    setSelected(characterId)
    setTab('character')
  }

  return (
    <Container size="xl" py="xl">
      <Group justify="space-between" align="flex-start" mb="md">
        <div>
          <Title order={1}>Skills</Title>
          <Text size="sm" c="dimmed">Skill points, attributes, training queues and skills across your characters.</Text>
        </div>
        <Group gap="xs">
          <Tooltip label={refresh.tooltip} disabled={!refresh.tooltip} multiline w={280}>
            <Button size="xs" variant="default" leftSection={refresh.tierIcon}
              onClick={() => refresh.mutate()} loading={refresh.isPending}>
              Refresh
            </Button>
          </Tooltip>
          <Button component={Link} to="/character-management" variant="subtle" leftSection={<IconArrowLeft size={14} />}>
            Back
          </Button>
        </Group>
      </Group>

      <Text size="xs" c="dimmed" mb="md">
        What this page may show is decided on the{' '}
        <Text component={Link} to="/character-management/characters" span c="accent" td="underline">Characters page</Text>.
        Skill names, groups and ranks come from the SDE cache; ranks appear after Admin applies an SDE refresh.
      </Text>

      {overview.isLoading ? <Loader color="accent" /> : characters.length === 0 ? (
        <Text c="dimmed">No ESI characters registered yet. Add one on the Characters page.</Text>
      ) : (
        <Tabs value={tab} onChange={setTab} keepMounted={false}>
          <Tabs.List mb="md">
            <Tabs.Tab value="overview">Overview</Tabs.Tab>
            <Tabs.Tab value="character">Character skills</Tabs.Tab>
            <Tabs.Tab value="matrix">Matrix</Tabs.Tab>
            {canDoctrine && <Tabs.Tab value="doctrine">Doctrine check</Tabs.Tab>}
          </Tabs.List>
          <Tabs.Panel value="overview">
            <QueueGuard />
            <OverviewTab rows={characters} onOpen={openCharacter} />
          </Tabs.Panel>
          <Tabs.Panel value="character">
            <CharacterTab characters={characters} selected={selected} onSelect={setSelected} />
          </Tabs.Panel>
          <Tabs.Panel value="matrix"><MatrixTab /></Tabs.Panel>
          {canDoctrine && <Tabs.Panel value="doctrine"><DoctrineCheckTab /></Tabs.Panel>}
        </Tabs>
      )}
    </Container>
  )
}
