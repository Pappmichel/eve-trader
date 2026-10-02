import { useState } from 'react'
import {
  ActionIcon, Alert, Autocomplete, Badge, Button, Container, Group, Loader, Modal, Progress, Select, Stack,
  Tabs, Text, Textarea, TextInput, Title, Tooltip,
} from '@mantine/core'
import { notify } from '../../../notify'
import { IconArrowDown, IconArrowLeft, IconArrowUp, IconTrash } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'

import { ApiError, charSkillPlansApi } from '../../../api/client'
import type { SkillPlan, SkillPlanStep } from '../../../api/types'
import { DataTable } from '../../../components/DataTable'
import { duration, qty } from '../../../format'
import { useCharacterSync } from '../../../hooks/useCharacterSync'

const KEY = ['char-skill-plans']
const LEVELS = ['1', '2', '3', '4', '5'].map((v, i) => ({ value: v, label: ['I', 'II', 'III', 'IV', 'V'][i] }))

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : 'Request failed.'
}

function useSaving(qc: ReturnType<typeof useQueryClient>, planId: number) {
  return {
    onSuccess: (plan: SkillPlan) => {
      qc.setQueryData([...KEY, 'plan', planId], plan)
      qc.invalidateQueries({ queryKey: [...KEY, 'list'] })
      qc.invalidateQueries({ queryKey: [...KEY, 'progress', planId] })
    },
    onError: (e: unknown) => notify({ title: 'Could not change the plan', message: message(e), color: 'danger' }),
  }
}

function AddSkill({ plan }: { plan: SkillPlan }) {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const [level, setLevel] = useState<string | null>('5')
  const trimmed = text.trim()
  const search = useQuery({
    queryKey: [...KEY, 'search', trimmed], queryFn: () => charSkillPlansApi.searchSkills(trimmed),
    enabled: trimmed.length >= 2, staleTime: 60_000,
  })
  const skills = search.data?.skills ?? []
  const match = skills.find((s) => s.name.toLowerCase() === trimmed.toLowerCase())
  const add = useMutation({
    mutationFn: () => charSkillPlansApi.addStep(plan.plan_id, match!.skill_id, Number(level)),
    ...useSaving(qc, plan.plan_id),
  })
  const onDone = (p: SkillPlan) => {
    setText('')
    notify({
      title: p.added ? `Added ${p.added} step${p.added === 1 ? '' : 's'}` : 'Nothing to add',
      message: p.added ? 'Lower levels and prerequisites are included.' : 'Those steps are already in the plan.', color: 'info',
    })
  }
  return (
    <Group align="flex-end" wrap="wrap">
      <Autocomplete label="Add a skill" placeholder="Type a skill name" value={text} onChange={setText}
        data={skills.map((s) => s.name)} limit={15} w={280} />
      <Select label="Up to level" data={LEVELS} value={level} onChange={setLevel} allowDeselect={false} w={110} />
      <Button disabled={!match || !level} loading={add.isPending}
        onClick={() => add.mutate(undefined, { onSuccess: onDone })}>
        Add with prerequisites
      </Button>
      {trimmed.length >= 2 && !search.isLoading && !match && (
        <Text size="xs" c="dimmed">Pick a skill from the list.</Text>
      )}
    </Group>
  )
}

function StepsTable({ plan }: { plan: SkillPlan }) {
  const qc = useQueryClient()
  const saving = useSaving(qc, plan.plan_id)
  const remove = useMutation({
    mutationFn: (s: SkillPlanStep) => charSkillPlansApi.removeStep(plan.plan_id, s.skill_id, s.level),
    ...saving,
    onSuccess: (p: SkillPlan) => {
      saving.onSuccess(p)
      if ((p.removed ?? 0) > 1) {
        notify({ title: `Removed ${p.removed} steps`, message: 'Steps that needed it were removed too.', color: 'info' })
      }
    },
  })
  const move = useMutation({
    mutationFn: (order: { skill_id: number; level: number }[]) => charSkillPlansApi.reorder(plan.plan_id, order),
    ...saving,
  })
  const swap = (i: number, j: number) => {
    const order = plan.steps.map((s) => ({ skill_id: s.skill_id, level: s.level }))
    ;[order[i], order[j]] = [order[j], order[i]]
    move.mutate(order)
  }
  if (plan.steps.length === 0) return <Text c="dimmed">This plan is empty. Add a skill above or import a plan.</Text>
  // `pos` is the step's place in the plan: the arrows move by plan position even
  // when the view is sorted differently.
  const steps = plan.steps.map((s, pos) => ({ ...s, pos }))
  const columns: ColumnDef<(typeof steps)[number], any>[] = [
    { header: '#', id: 'pos', size: 60, accessorFn: (s) => s.pos + 1 },
    { header: 'Skill', accessorKey: 'name', size: 260 },
    { header: 'Level', accessorKey: 'level_label', size: 90 },
    { header: 'Group', id: 'group', size: 180, accessorFn: (s) => s.group_name ?? '–' },
    {
      header: '', id: 'actions', size: 130, enableSorting: false, enableResizing: false,
      cell: (i) => {
        const s = i.row.original
        return (
          <Group gap={4} justify="flex-end" wrap="nowrap">
            <ActionIcon variant="subtle" aria-label={`Move ${s.name} ${s.level_label} up`} disabled={s.pos === 0 || move.isPending}
              onClick={() => swap(s.pos, s.pos - 1)}><IconArrowUp size={14} /></ActionIcon>
            <ActionIcon variant="subtle" aria-label={`Move ${s.name} ${s.level_label} down`}
              disabled={s.pos === plan.steps.length - 1 || move.isPending}
              onClick={() => swap(s.pos, s.pos + 1)}><IconArrowDown size={14} /></ActionIcon>
            <Tooltip label="Also removes steps that need this one" multiline w={200}>
              <ActionIcon variant="subtle" color="danger" aria-label={`Remove ${s.name} ${s.level_label}`}
                onClick={() => remove.mutate(s)}><IconTrash size={14} /></ActionIcon>
            </Tooltip>
          </Group>
        )
      },
    },
  ]
  return (
    <DataTable
      data={steps} columns={columns} maxHeight={560}
      getRowId={(s) => `${s.skill_id}-${s.level}`} exportFilename={`skill-plan-${plan.plan_id}`}
    />
  )
}

function ProgressTab({ planId, empty }: { planId: number; empty: boolean }) {
  const { data, isLoading, error } = useQuery({
    queryKey: [...KEY, 'progress', planId], queryFn: () => charSkillPlansApi.progress(planId), retry: false,
  })
  const refresh = useCharacterSync(
    'Skill plan refresh', charSkillPlansApi.sync, [KEY],
    'Fetches the current skills and attributes from ESI for every character shared with Skill Plans.',
  )
  if (isLoading) return <Loader color="accent" />
  if (error || !data) return <Text c="danger">{message(error)}</Text>
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text size="xs" c="dimmed">
          Training times are estimates from each character&apos;s current attributes, without implants or boosters.
          Which characters appear is decided on the Characters page.
        </Text>
        <Tooltip label={refresh.tooltip} disabled={!refresh.tooltip} multiline w={280}>
          <Button size="xs" variant="default" leftSection={refresh.tierIcon} onClick={() => refresh.mutate()} loading={refresh.isPending}>
            Refresh skills
          </Button>
        </Tooltip>
      </Group>
      {empty && <Text c="dimmed">The plan has no steps yet.</Text>}
      {data.characters.length === 0 && (
        <Text c="dimmed">No character is shared with Skill Plans. Tick it for a character on the Characters page.</Text>
      )}
      {data.characters.map((c) => (
        <div key={c.character_id}>
          <Group justify="space-between" mb={4}>
            <Text fw={600}>{c.character_name}</Text>
            {c.synced ? (
              <Text size="sm">
                {c.steps_done}/{c.steps_total} steps
                {c.train_seconds === null || c.train_seconds === undefined ? '' : ` · ~${duration(c.train_seconds)} left`}
                {c.sp_remaining ? ` · ${qty(c.sp_remaining)} SP` : ''}
              </Text>
            ) : <Badge color="warn" variant="light">not synced - press Refresh skills</Badge>}
          </Group>
          {c.synced && (
            <>
              <Progress value={c.steps_total ? ((c.steps_done ?? 0) / c.steps_total) * 100 : 0} />
              {(c.next_steps ?? []).length > 0 && (
                <Text size="xs" c="dimmed" mt={4}>
                  Next: {(c.next_steps ?? []).map((n) => `${n.name} ${n.level_label}`).join(' → ')}
                </Text>
              )}
              {c.steps_total !== 0 && c.steps_done === c.steps_total && <Text size="xs" c="green">Plan complete.</Text>}
            </>
          )}
        </div>
      ))}
      {data.hidden_characters.length > 0 && (
        <Text size="xs" c="dimmed">Not shown (Skill Plans not shared): {data.hidden_characters.map((c) => c.character_name).join(', ')}</Text>
      )}
    </Stack>
  )
}

function PlanEditor({ planId, onDeleted }: { planId: number; onDeleted: () => void }) {
  const qc = useQueryClient()
  const { data: plan, isLoading, error } = useQuery({
    queryKey: [...KEY, 'plan', planId], queryFn: () => charSkillPlansApi.get(planId), retry: false,
  })
  const [exportText, setExportText] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [renaming, setRenaming] = useState<{ name: string; description: string } | null>(null)
  const rename = useMutation({
    mutationFn: (v: { name: string; description: string }) => charSkillPlansApi.update(planId, v.name, v.description),
    ...useSaving(qc, planId), onSuccess: (p: SkillPlan) => {
      qc.setQueryData([...KEY, 'plan', planId], p)
      qc.invalidateQueries({ queryKey: [...KEY, 'list'] })
      setRenaming(null)
    },
  })
  const del = useMutation({
    mutationFn: () => charSkillPlansApi.remove(planId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: [...KEY, 'list'] }); setConfirmDelete(false); onDeleted() },
    onError: (e) => notify({ title: 'Could not delete', message: message(e), color: 'danger' }),
  })
  const doExport = useMutation({
    mutationFn: () => charSkillPlansApi.exportText(planId), onSuccess: (r) => setExportText(r.text),
    onError: (e) => notify({ title: 'Could not export', message: message(e), color: 'danger' }),
  })

  if (isLoading) return <Loader color="accent" />
  if (error || !plan) return <Text c="danger">{message(error)}</Text>
  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={3}>{plan.name}</Title>
          {plan.description && <Text size="sm" c="dimmed">{plan.description}</Text>}
        </div>
        <Group gap="xs">
          <Button size="xs" variant="default" onClick={() => setRenaming({ name: plan.name, description: plan.description })}>Rename</Button>
          <Button size="xs" variant="default" onClick={() => doExport.mutate()} loading={doExport.isPending}>Export</Button>
          <Button size="xs" variant="default" color="danger" onClick={() => setConfirmDelete(true)}>Delete</Button>
        </Group>
      </Group>
      {!plan.sde_ready && (
        <Alert color="yellow" title="Skill data is not loaded yet">
          Ask an admin to run an SDE preview and apply it once. Until then skills cannot be searched or added.
        </Alert>
      )}
      <Tabs defaultValue="steps" keepMounted={false}>
        <Tabs.List mb="md">
          <Tabs.Tab value="steps">Steps ({plan.steps.length})</Tabs.Tab>
          <Tabs.Tab value="progress">Progress</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="steps"><Stack gap="md"><AddSkill plan={plan} /><StepsTable plan={plan} /></Stack></Tabs.Panel>
        <Tabs.Panel value="progress"><ProgressTab planId={planId} empty={plan.steps.length === 0} /></Tabs.Panel>
      </Tabs>

      <Modal opened={exportText !== null} onClose={() => setExportText(null)} title="Export plan" size="md">
        <Textarea readOnly autosize minRows={6} maxRows={20} value={exportText ?? ''} aria-label="Plan text" />
        <Text size="xs" c="dimmed" mt="xs">One line per step: skill name and level. Import accepts roman numerals or digits.</Text>
      </Modal>
      <Modal opened={renaming !== null} onClose={() => setRenaming(null)} title="Rename plan">
        <Stack>
          <TextInput label="Name" value={renaming?.name ?? ''} maxLength={100}
            onChange={(e) => { const v = e.currentTarget.value; setRenaming((r) => (r ? { ...r, name: v } : r)) }} />
          <Textarea label="Description" value={renaming?.description ?? ''} maxLength={500}
            onChange={(e) => { const v = e.currentTarget.value; setRenaming((r) => (r ? { ...r, description: v } : r)) }} />
          <Button loading={rename.isPending} disabled={!renaming?.name.trim()} onClick={() => renaming && rename.mutate(renaming)}>Save</Button>
        </Stack>
      </Modal>
      <Modal opened={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete this plan?">
        <Stack>
          <Text size="sm">“{plan.name}” and its {plan.steps.length} steps will be deleted. This cannot be undone.</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirmDelete(false)}>Cancel</Button>
            <Button color="danger" loading={del.isPending} onClick={() => del.mutate()}>Delete plan</Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  )
}

function NewPlanModal({ opened, onClose, onCreated }: {
  opened: boolean; onClose: () => void; onCreated: (plan: SkillPlan) => void
}) {
  const [name, setName] = useState('')
  const [text, setText] = useState('')
  const create = useMutation({
    mutationFn: () => charSkillPlansApi.create(name.trim(), '', text),
    onSuccess: (plan) => {
      setName(''); setText('')
      const notes: string[] = []
      if (plan.steps_added_for_prerequisites) notes.push(`${plan.steps_added_for_prerequisites} prerequisite step(s) added`)
      if (plan.unresolved?.length) notes.push(`${plan.unresolved.length} line(s) not recognised: ${plan.unresolved.slice(0, 3).join('; ')}`)
      if (notes.length) notify({ title: 'Plan imported', message: notes.join(' · '), color: 'info' })
      onCreated(plan)
    },
    onError: (e) => notify({ title: 'Could not create the plan', message: message(e), color: 'danger' }),
  })
  return (
    <Modal opened={opened} onClose={onClose} title="New skill plan" size="md">
      <Stack>
        <TextInput label="Name" value={name} maxLength={100} onChange={(e) => setName(e.currentTarget.value)} data-autofocus />
        <Textarea label="Import from plan text (optional)" placeholder={'Gunnery V\nSmall Projectile Turret IV'}
          autosize minRows={4} maxRows={12} value={text} onChange={(e) => setText(e.currentTarget.value)} />
        <Text size="xs" c="dimmed">One skill per line, level as roman numerals or a digit. Missing prerequisites are added.</Text>
        <Button loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>Create plan</Button>
      </Stack>
    </Modal>
  )
}

export default function SkillPlansPage() {
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const qc = useQueryClient()
  const list = useQuery({ queryKey: [...KEY, 'list'], queryFn: charSkillPlansApi.list })
  const plans = list.data?.plans ?? []
  const current = selected && plans.some((p) => String(p.plan_id) === selected) ? selected : (plans[0] ? String(plans[0].plan_id) : null)

  return (
    <Container size="xl" py="xl">
      <Group justify="space-between" align="flex-start" mb="md">
        <div>
          <Title order={1}>Skill Plans</Title>
          <Text size="sm" c="dimmed">Plan skills with their prerequisites filled in, and see how far each character is.</Text>
        </div>
        <Button component={Link} to="/character-management" variant="subtle" leftSection={<IconArrowLeft size={14} />}>Back</Button>
      </Group>

      {list.isLoading ? <Loader color="accent" /> : (
        <Stack gap="md">
          <Group align="flex-end">
            <Select label="Plan" placeholder="No plans yet" value={current} onChange={setSelected} allowDeselect={false}
              data={plans.map((p) => ({ value: String(p.plan_id), label: `${p.name} (${p.step_count})` }))} w={320} />
            <Button onClick={() => setCreating(true)}>New plan</Button>
          </Group>
          {current
            ? <PlanEditor key={current} planId={Number(current)} onDeleted={() => setSelected(null)} />
            : <Text c="dimmed">Create a plan to get started.</Text>}
        </Stack>
      )}
      <NewPlanModal opened={creating} onClose={() => setCreating(false)} onCreated={(p) => {
        setCreating(false)
        qc.invalidateQueries({ queryKey: [...KEY, 'list'] })
        setSelected(String(p.plan_id))
      }} />
    </Container>
  )
}
