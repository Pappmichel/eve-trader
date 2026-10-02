import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charSkillPlansApi } from '../../../api/client'
import type { SkillPlan } from '../../../api/types'
import SkillPlansPage from './SkillPlansPage'

vi.mock('../../../api/client', () => ({
  charSkillPlansApi: {
    list: vi.fn(), create: vi.fn(), get: vi.fn(), update: vi.fn(), remove: vi.fn(), addStep: vi.fn(),
    removeStep: vi.fn(), reorder: vi.fn(), exportText: vi.fn(), searchSkills: vi.fn(), progress: vi.fn(), sync: vi.fn(),
  },
  ApiError: class ApiError extends Error {},
}))

function step(position: number, skill_id: number, name: string, level: number) {
  return { position, skill_id, level, level_label: ['', 'I', 'II', 'III', 'IV', 'V'][level], name, group_name: 'Gunnery', rank: 1 }
}

function plan(overrides: Partial<SkillPlan> = {}): SkillPlan {
  return {
    plan_id: 1, name: 'Frigates', description: '', created_at: null, updated_at: null, sde_ready: true,
    steps: [step(0, 10, 'Gunnery', 1), step(1, 10, 'Gunnery', 2), step(2, 11, 'Small Projectile Turret', 1)],
    ...overrides,
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <Notifications />
      <QueryClientProvider client={client}>
        <MemoryRouter><SkillPlansPage /></MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

const summary = { plan_id: 1, name: 'Frigates', description: '', created_at: null, updated_at: null, step_count: 3 }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(charSkillPlansApi.list).mockResolvedValue({ plans: [summary], sde_ready: true })
  vi.mocked(charSkillPlansApi.get).mockResolvedValue(plan())
  vi.mocked(charSkillPlansApi.progress).mockResolvedValue({ plan_id: 1, characters: [], hidden_characters: [] })
})

describe('Skill Plans page', () => {
  it('invites you to create the first plan', async () => {
    vi.mocked(charSkillPlansApi.list).mockResolvedValue({ plans: [], sde_ready: true })
    renderPage()
    expect(await screen.findByText('Create a plan to get started.')).toBeInTheDocument()
  })

  it('shows the steps in order and warns when the SDE has no skill data', async () => {
    vi.mocked(charSkillPlansApi.get).mockResolvedValue(plan({ sde_ready: false }))
    renderPage()
    expect(await screen.findByText('Small Projectile Turret')).toBeInTheDocument()
    expect(screen.getByText('Skill data is not loaded yet')).toBeInTheDocument()
    const rows = screen.getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[1].textContent)
    expect(rows).toEqual(['Gunnery', 'Gunnery', 'Small Projectile Turret'])
  })

  it('moves a step by sending the whole new order, and disables the ends', async () => {
    vi.mocked(charSkillPlansApi.reorder).mockResolvedValue(plan())
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Small Projectile Turret')
    expect(screen.getByRole('button', { name: 'Move Gunnery I up' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move Small Projectile Turret I down' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Move Gunnery I down' }))
    expect(charSkillPlansApi.reorder).toHaveBeenCalledWith(1, [
      { skill_id: 10, level: 2 }, { skill_id: 10, level: 1 }, { skill_id: 11, level: 1 },
    ])
  })

  it('removes a step and says when dependants went with it', async () => {
    vi.mocked(charSkillPlansApi.removeStep).mockResolvedValue(plan({ steps: [step(0, 10, 'Gunnery', 1)], removed: 3 }))
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Small Projectile Turret')
    await user.click(screen.getByRole('button', { name: 'Remove Gunnery II' }))
    expect(charSkillPlansApi.removeStep).toHaveBeenCalledWith(1, 10, 2)
    expect(await screen.findByText('Removed 3 steps')).toBeInTheDocument()
  })

  it('adds a searched skill up to the chosen level', async () => {
    vi.mocked(charSkillPlansApi.searchSkills).mockResolvedValue({ skills: [{ skill_id: 12, name: 'Rapid Firing', group_name: 'Gunnery' }] })
    vi.mocked(charSkillPlansApi.addStep).mockResolvedValue(plan({ added: 4 }))
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Small Projectile Turret')
    const add = screen.getByRole('button', { name: 'Add with prerequisites' })
    expect(add).toBeDisabled()
    await user.type(screen.getByPlaceholderText('Type a skill name'), 'Rapid Firing')
    await waitFor(() => expect(add).toBeEnabled())
    await user.click(add)
    expect(charSkillPlansApi.addStep).toHaveBeenCalledWith(1, 12, 5)
    expect(await screen.findByText('Added 4 steps')).toBeInTheDocument()
  })

  it('creates a plan from pasted text and reports what could not be used', async () => {
    vi.mocked(charSkillPlansApi.create).mockResolvedValue(plan({
      plan_id: 2, name: 'Imported', unresolved: ['Bogus V'], steps_added_for_prerequisites: 2,
    }))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'New plan' }))
    const create = await screen.findByRole('button', { name: 'Create plan' })
    expect(create).toBeDisabled()
    await user.type(screen.getByLabelText('Name'), 'Imported')
    await user.type(screen.getByLabelText(/Import from plan text/), 'Gunnery V')
    await user.click(create)
    expect(charSkillPlansApi.create).toHaveBeenCalledWith('Imported', '', 'Gunnery V')
    expect(await screen.findByText(/2 prerequisite step\(s\) added/)).toBeInTheDocument()
    expect(screen.getByText(/1 line\(s\) not recognised: Bogus V/)).toBeInTheDocument()
  })

  it('exports the plan text and deletes only after a confirmation', async () => {
    vi.mocked(charSkillPlansApi.exportText).mockResolvedValue({ name: 'Frigates', text: 'Gunnery I\nGunnery II' })
    vi.mocked(charSkillPlansApi.remove).mockResolvedValue({ deleted: 1 })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Small Projectile Turret')
    await user.click(screen.getByRole('button', { name: 'Export' }))
    expect(await screen.findByLabelText('Plan text')).toHaveValue('Gunnery I\nGunnery II')
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(charSkillPlansApi.remove).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: 'Delete plan' }))
    expect(charSkillPlansApi.remove).toHaveBeenCalledWith(1)
  })

  it('shows per-character progress, the next steps and unsynced characters', async () => {
    vi.mocked(charSkillPlansApi.progress).mockResolvedValue({
      plan_id: 1,
      characters: [
        {
          character_id: 1, character_name: 'Alice', synced: true, steps_total: 3, steps_done: 2, sp_remaining: 2329,
          train_seconds: 3660, next_steps: [{ skill_id: 11, level: 1, level_label: 'I', name: 'Small Projectile Turret', sp_remaining: 2329 }],
        },
        { character_id: 2, character_name: 'Bob', synced: false },
      ],
      hidden_characters: [{ character_id: 3, character_name: 'Carol' }],
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'Progress' }))
    expect(await screen.findByText(/2\/3 steps · ~1h 1m left · 2,329 SP/)).toBeInTheDocument()
    expect(screen.getByText(/Next: Small Projectile Turret I/)).toBeInTheDocument()
    expect(screen.getByText(/not synced - press Refresh skills/)).toBeInTheDocument()
    expect(screen.getByText(/Carol/)).toBeInTheDocument()
  })
})
