import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charSkillsApi, gateApi } from '../../../api/client'
import type { CharacterSkills, SkillMatrix, SkillsOverviewRow } from '../../../api/types'
import SkillsPage from './SkillsPage'

vi.mock('../../../api/client', () => ({
  charSkillsApi: {
    overview: vi.fn(), character: vi.fn(), matrix: vi.fn(), sync: vi.fn(), warnings: vi.fn(), settings: vi.fn(),
    setSettings: vi.fn(), doctrineCheck: vi.fn(),
  },
  gateApi: { status: vi.fn() },
  ApiError: class ApiError extends Error {},
}))

const notShared = { state: 'not_shared', value: null } as const
const ATTRS = {
  charisma: 19, intelligence: 27, memory: 21, perception: 20, willpower: 22,
  bonus_remaps: 1, last_remap_date: null, accrued_remap_cooldown_date: null,
}

function row(overrides: Partial<SkillsOverviewRow> = {}): SkillsOverviewRow {
  return {
    character_id: 1, character_name: 'Alice', summary: notShared, queue: notShared, freshness: {},
    ...overrides,
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <Notifications />
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SkillsPage />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(charSkillsApi.warnings).mockResolvedValue({ count: 0, characters: [], queue_warning_hours: 24 })
  vi.mocked(gateApi.status).mockResolvedValue({ tools: ['char_skills'] } as never)
})

const runningQueue = {
  entries: [{
    queue_position: 0, skill_id: 3386, name: 'Mining', finished_level: 4,
    start_date: null, finish_date: '2099-01-01T00:00:00Z',
    training_start_sp: null, level_start_sp: null, level_end_sp: null,
  }],
  length: 1, empty: false, paused: false, current: null, ends_at: '2099-01-01T00:00:00Z', warning: null,
}

describe('Skills page overview', () => {
  it('shows SP totals, an extractable estimate, attributes and the queue, and hides what is not shared', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({
      characters: [
        row({
          summary: {
            state: 'ok',
            value: { total_sp: 5_500_000, unallocated_sp: 1234, extractable_estimate: 1, attributes: ATTRS },
          },
          queue: { state: 'ok', value: { ...runningQueue, current: runningQueue.entries[0] } },
        }),
        row({ character_id: 2, character_name: 'Bob' }),
      ],
    })
    renderPage()

    expect(await screen.findByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('5,500,000')).toBeInTheDocument()
    expect(screen.getByText('1,234')).toBeInTheDocument()
    expect(screen.getByText('1×')).toBeInTheDocument()
    expect(screen.getByText(/INT 27 · MEM 21 · PER 20 · WIL 22 · CHA 19/)).toBeInTheDocument()
    expect(screen.getByText(/Mining IV/)).toBeInTheDocument()
    // Bob shares nothing: every cell says so instead of showing 0 / blanks.
    expect(screen.getAllByText('not shared').length).toBeGreaterThanOrEqual(5)
  })

  it('flags an empty and a paused queue instead of showing a date', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({
      characters: [
        row({ queue: { state: 'ok', value: { entries: [], length: 0, empty: true, paused: false, current: null, ends_at: null, warning: { kind: 'empty', hours_left: null } } } }),
        row({ character_id: 2, character_name: 'Bob', queue: { state: 'ok', value: { ...runningQueue, paused: true, ends_at: null, warning: { kind: 'paused', hours_left: null } } } }),
      ],
    })
    renderPage()
    expect(await screen.findByText('queue empty')).toBeInTheDocument()
    expect(screen.getByText('queue paused')).toBeInTheDocument()
  })

  it('says so when no character is registered', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({ characters: [] })
    renderPage()
    expect(await screen.findByText(/No ESI characters registered yet/)).toBeInTheDocument()
  })

  it('Refresh syncs, reports an already-running sync, and refetches the overview', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({ characters: [row()] })
    vi.mocked(charSkillsApi.sync).mockResolvedValue({ ok: true, characters: {}, in_flight: [1], failed: [] })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Alice')
    expect(charSkillsApi.overview).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: /Refresh/ }))
    expect(await screen.findByText('Sync already running')).toBeInTheDocument()
    await waitFor(() => expect(charSkillsApi.overview).toHaveBeenCalledTimes(2))
  })
})

describe('Skills page character tab', () => {
  const detail: CharacterSkills = {
    character_id: 1, character_name: 'Alice', freshness: {},
    summary: {
      state: 'ok',
      value: { total_sp: 5_500_000, unallocated_sp: 0, extractable_estimate: 1, attributes: ATTRS },
    },
    queue: { state: 'ok', value: runningQueue },
    skills: {
      state: 'ok',
      value: [{
        group_id: 268, group_name: 'Industry', total_sp: 306_000, maxed: 1,
        skills: [
          { skill_id: 3380, name: 'Industry', rank: 1, active_level: 5, trained_level: 5, skillpoints: 256_000, sp_to_level_v: 0 },
          { skill_id: 3387, name: 'Mass Production', rank: null, active_level: 3, trained_level: 4, skillpoints: 50_000, sp_to_level_v: null },
        ],
      }],
    },
  }

  it('opens from the overview and lists skill groups with level, rank, SP and SP to V', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({ characters: [row()] })
    vi.mocked(charSkillsApi.character).mockResolvedValue(detail)
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Alice')
    await user.click(screen.getByRole('button', { name: 'Skills' }))

    expect(await screen.findByText('Industry', { selector: 'p' })).toBeInTheDocument()
    expect(charSkillsApi.character).toHaveBeenCalledWith(1)
    await user.click(screen.getByRole('button', { name: /Industry/ }))
    expect(await screen.findByText('Mass Production')).toBeInTheDocument()
    // Unknown rank (no SDE meta yet) renders as a dash, not as 0 or undefined.
    const massProduction = screen.getByText('Mass Production').closest('tr') as HTMLElement
    expect(within(massProduction).getAllByText('–').length).toBe(2)
    // Level IV trained but only III active is called out.
    expect(within(massProduction).getByText('IV')).toBeInTheDocument()
    expect(within(massProduction).getByText(/\(III\)/)).toBeInTheDocument()
  })
})

describe('Skills page matrix tab', () => {
  const matrix: SkillMatrix = {
    characters: [
      { character_id: 1, character_name: 'Alice' },
      { character_id: 2, character_name: 'Bob' },
    ],
    hidden_characters: [{ character_id: 3, character_name: 'Carol' }],
    reauth_needed: [2],
    groups: [{
      group_id: 268, group_name: 'Industry',
      skills: [
        { skill_id: 3380, name: 'Industry', levels: { '1': { active: 5, trained: 5 } } },
        { skill_id: 3387, name: 'Mass Production', levels: { '1': { active: 3, trained: 4 }, '2': { active: 1, trained: 1 } } },
      ],
    }],
  }

  it('shows one column per shared character, the hidden ones, and filters skills by name', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({ characters: [row()] })
    vi.mocked(charSkillsApi.matrix).mockResolvedValue(matrix)
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Alice')
    await user.click(screen.getByRole('tab', { name: 'Matrix' }))

    expect(await screen.findByText(/Not shared, so no column: Carol/)).toBeInTheDocument()
    expect(screen.getByText(/needs a re-authorize on the Characters page:\s*Bob/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Industry/ }))
    const columnHeaders = (await screen.findAllByRole('columnheader')).map((h) => h.textContent)
    expect(columnHeaders).toEqual(['Skill', 'Alice', 'Bob'])
    const industryRow = screen.getByText('Industry', { selector: 'td' }).closest('tr') as HTMLElement
    expect(within(industryRow).getByText('V')).toBeInTheDocument()
    expect(within(industryRow).getByText('–')).toBeInTheDocument()      // Bob has not trained it

    await user.type(screen.getByPlaceholderText('Filter skills'), 'mass')
    expect(screen.queryByText('Industry', { selector: 'td' })).not.toBeInTheDocument()
    expect(screen.getByText('Mass Production')).toBeInTheDocument()
  })

  it('says so when nobody shares Skills', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({ characters: [row()] })
    vi.mocked(charSkillsApi.matrix).mockResolvedValue({ characters: [], hidden_characters: [], reauth_needed: [], groups: [] })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Alice')
    await user.click(screen.getByRole('tab', { name: 'Matrix' }))
    expect(await screen.findByText(/No character shares Skills yet/)).toBeInTheDocument()
  })
})

describe('Queue guard (phase 5a)', () => {
  const soon = {
    ...runningQueue, warning: { kind: 'ends_soon', hours_left: 5.5 } as const,
    current: runningQueue.entries[0],
  }

  it('flags a queue that ends soon on its row and lists it in a banner', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({ characters: [
      row({ queue: { state: 'ok', value: soon } }),
      row({ character_id: 2, character_name: 'Bob', queue: { state: 'ok', value: { ...runningQueue, current: runningQueue.entries[0] } } }),
    ] })
    vi.mocked(charSkillsApi.warnings).mockResolvedValue({
      count: 1, queue_warning_hours: 24,
      characters: [{ character_id: 1, character_name: 'Alice', kind: 'ends_soon', hours_left: 5.5 }],
    })
    renderPage()
    expect(await screen.findByText('Skill queues need attention')).toBeInTheDocument()
    expect(screen.getByText('ends in 5.5 h')).toBeInTheDocument()          // the row badge
    expect(screen.getByText('Alice: ends in 5.5 h')).toBeInTheDocument()   // the banner line
  })

  it('shows no banner when nothing needs attention', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({ characters: [row()] })
    renderPage()
    await screen.findByText('Alice')
    expect(screen.queryByText('Skill queues need attention')).not.toBeInTheDocument()
  })

  it('saves a new warning threshold (0 turns warnings off) and refetches', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue({ characters: [row()] })
    vi.mocked(charSkillsApi.setSettings).mockResolvedValue({ queue_warning_hours: 48 })
    const user = userEvent.setup()
    renderPage()
    const input = await screen.findByLabelText('Warn when a queue ends within (hours)')
    await waitFor(() => expect(input).toHaveValue('24'))
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()          // unchanged
    await user.clear(input)
    await user.type(input, '48')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(charSkillsApi.setSettings).toHaveBeenCalledWith(48))
    await waitFor(() => expect(charSkillsApi.warnings).toHaveBeenCalledTimes(2))
  })
})

describe('Skills page doctrine check tab', () => {
  const overview = { characters: [row()] }

  it('is hidden without the doctrine grant and shown with it', async () => {
    vi.mocked(charSkillsApi.overview).mockResolvedValue(overview)
    renderPage()
    await screen.findByRole('tab', { name: 'Matrix' })
    expect(screen.queryByRole('tab', { name: 'Doctrine check' })).toBeNull()
  })

  it('lists who can fly what and expands the missing skills', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ tools: ['char_skills', 'doctrine'] } as never)
    vi.mocked(charSkillsApi.overview).mockResolvedValue(overview)
    vi.mocked(charSkillsApi.doctrineCheck).mockResolvedValue({
      sde_ready: true,
      characters: [{ character_id: 1, character_name: 'Alice' }, { character_id: 2, character_name: 'Bob' }],
      hidden_characters: [{ character_id: 3, character_name: 'Carol' }],
      fittings: [{
        fitting_id: 'f1', name: 'Rifter AC', variant_label: null, doctrine_id: 'd1', doctrine_name: 'Frigates',
        hull_type_id: 587, hull_name: 'Rifter', required_skills: 3,
        characters: [
          { character_id: 1, can_fly: true, missing: [], train_seconds: 0 },
          {
            character_id: 2, can_fly: false, train_seconds: 3600,
            missing: [{ skill_id: 1, name: 'Small Projectile Turret', needed: 3, have: 1, sp_remaining: 14000 }],
          },
        ],
      }],
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'Doctrine check' }))
    expect(await screen.findByText('Can fly')).toBeTruthy()
    expect(screen.getByText(/1 missing/)).toBeTruthy()
    expect(screen.getByText(/Carol/)).toBeTruthy()
    await user.click(screen.getByText('Rifter AC'))
    expect(await screen.findByText(/Small Projectile Turret III \(has I\)/)).toBeTruthy()
  })

  it('explains an SDE without skill requirements', async () => {
    vi.mocked(gateApi.status).mockResolvedValue({ tools: ['char_skills', 'doctrine'] } as never)
    vi.mocked(charSkillsApi.overview).mockResolvedValue(overview)
    vi.mocked(charSkillsApi.doctrineCheck).mockResolvedValue({
      sde_ready: false, fittings: [], characters: [], hidden_characters: [],
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('tab', { name: 'Doctrine check' }))
    expect(await screen.findByText(/not loaded yet/)).toBeTruthy()
  })
})
