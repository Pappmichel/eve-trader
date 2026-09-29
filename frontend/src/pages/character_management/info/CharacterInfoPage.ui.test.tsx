import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charInfoApi } from '../../../api/client'
import type { CharInfoCharacter } from '../../../api/types'
import CharacterInfoPage from './CharacterInfoPage'

vi.mock('../../../api/client', () => ({
  charInfoApi: { overview: vi.fn(), detail: vi.fn(), sync: vi.fn() },
  ApiError: class ApiError extends Error {},
}))

const notShared = { state: 'not_shared', value: null } as const

function character(overrides: Partial<CharInfoCharacter> = {}): CharInfoCharacter {
  return {
    character_id: 1, character_name: 'Alice', corporation_id: 9, corporation_name: 'Test Corp',
    alliance_id: null, alliance_name: null, security_status: 1.234, birthday: null,
    wallet_balance: notShared, location: notShared, ship: notShared, online: notShared,
    freshness: {},
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
          <CharacterInfoPage />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

beforeEach(() => vi.resetAllMocks())

describe('Character Info page', () => {
  it('renders each field state: ok, not shared, re-auth needed, not synced, error', async () => {
    vi.mocked(charInfoApi.overview).mockResolvedValue({
      characters: [
        character({
          wallet_balance: { state: 'ok', value: 1234567.4, synced_at: '2026-09-29T08:00:00Z' },
          online: { state: 'ok', value: { online: true } },
          location: {
            state: 'ok',
            value: {
              solar_system_id: 30000142, solar_system_name: 'Jita', location_id: 60003760,
              location_kind: 'station', location_name: 'Jita IV - Moon 4',
            },
          },
          ship: { state: 'reauth_needed', value: null },
        }),
        character({
          character_id: 2, character_name: 'Bob',
          wallet_balance: { state: 'not_synced', value: null },
          online: { state: 'error', value: null, detail: 'HTTP 403 for online' },
        }),
      ],
    })
    renderPage()

    expect(await screen.findByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('1,234,567 ISK')).toBeInTheDocument()
    expect(screen.getByText('online')).toBeInTheDocument()
    expect(screen.getByText('Jita')).toBeInTheDocument()
    expect(screen.getByText('Jita IV - Moon 4')).toBeInTheDocument()
    expect(screen.getByText('re-auth needed')).toBeInTheDocument()
    expect(screen.getByText('not synced yet')).toBeInTheDocument()
    expect(screen.getByText('error')).toBeInTheDocument()
    // Bob shares no location/ship; Alice shares no ... (all not_shared cells)
    expect(screen.getAllByText('not shared').length).toBeGreaterThan(0)
  })

  it('says so when no character is registered', async () => {
    vi.mocked(charInfoApi.overview).mockResolvedValue({ characters: [] })
    renderPage()
    expect(await screen.findByText(/No ESI characters registered yet/)).toBeInTheDocument()
  })

  it('Refresh syncs, tells the user about an already-running sync, and refetches', async () => {
    vi.mocked(charInfoApi.overview).mockResolvedValue({ characters: [character()] })
    vi.mocked(charInfoApi.sync).mockResolvedValue({ ok: true, characters: {}, in_flight: [1], failed: [] })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Alice')
    expect(charInfoApi.overview).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /Refresh/ }))
    expect(await screen.findByText('Sync already running')).toBeInTheDocument()
    await waitFor(() => expect(charInfoApi.overview).toHaveBeenCalledTimes(2))
  })

  it('opens the detail drawer with standings, loyalty points and history', async () => {
    vi.mocked(charInfoApi.overview).mockResolvedValue({ characters: [character()] })
    vi.mocked(charInfoApi.detail).mockResolvedValue(character({
      standings: {
        state: 'ok',
        value: {
          faction: [{ from_id: 500001, name: 'Caldari State', standing: 5.5 }],
          npc_corp: [{ from_id: 1000035, name: 'Caldari Navy', standing: -2 }],
          agent: [],
        },
      },
      loyalty_points: {
        state: 'ok',
        value: [{ corporation_id: 1000035, corporation_name: 'Caldari Navy', loyalty_points: 12345 }],
      },
      corporation_history: [{ corporation_id: 9, corporation_name: 'Test Corp', start_date: '2020-01-01T00:00:00Z' }],
    }))
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Alice')
    await user.click(screen.getByRole('button', { name: 'Details' }))

    expect(await screen.findByText('Caldari State')).toBeInTheDocument()
    expect(screen.getByText('5.50')).toBeInTheDocument()
    expect(screen.getByText('-2.00')).toBeInTheDocument()
    expect(screen.getByText('12,345 LP')).toBeInTheDocument()
    expect(charInfoApi.detail).toHaveBeenCalledWith(1)
  })

  it('shows implants, the home location and jump clones with their implants', async () => {
    vi.mocked(charInfoApi.overview).mockResolvedValue({ characters: [character()] })
    vi.mocked(charInfoApi.detail).mockResolvedValue(character({
      implants: { state: 'ok', value: [{ type_id: 9941, name: 'Genolution Core Augmentation CA-1' }] },
      clones: {
        state: 'ok',
        value: {
          home: { location_id: 60003760, location_type: 'station', location_name: 'Jita IV - Moon 4' },
          last_clone_jump_date: null,
          last_station_change_date: null,
          jump_clones: [
            {
              jump_clone_id: 7, name: null, location_id: 60008494, location_type: 'station',
              location_name: 'Amarr VIII', implants: [{ type_id: 9899, name: 'Ocular Filter' }],
            },
            {
              jump_clone_id: 8, name: 'Mining', location_id: 1035466617946, location_type: 'structure',
              location_name: null, implants: [],
            },
          ],
        },
      },
    }))
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Alice')
    await user.click(screen.getByRole('button', { name: 'Details' }))

    expect(await screen.findByText('Genolution Core Augmentation CA-1')).toBeInTheDocument()
    expect(screen.getByText(/Home: Jita IV - Moon 4/)).toBeInTheDocument()
    expect(screen.getByText('Amarr VIII')).toBeInTheDocument()
    expect(screen.getByText('Ocular Filter')).toBeInTheDocument()
    expect(screen.getByText(/Mining – structure #1035466617946/)).toBeInTheDocument()   // unresolved keeps its id
    expect(screen.getByText('No implants.')).toBeInTheDocument()
  })

  it('shows a jump fatigue countdown per character and the next clone jump in the detail', async () => {
    const soon = new Date(Date.now() + 90 * 60_000).toISOString()
    vi.mocked(charInfoApi.overview).mockResolvedValue({
      characters: [
        character({
          fatigue: { state: 'ok', value: { jump_fatigue_expire_date: soon, last_jump_date: null, last_update_date: null } },
        }),
        character({ character_id: 2, character_name: 'Bob', fatigue: { state: 'ok', value: {
          jump_fatigue_expire_date: null, last_jump_date: null, last_update_date: null } } }),
      ],
    })
    vi.mocked(charInfoApi.detail).mockResolvedValue(character({
      clones: {
        state: 'ok',
        value: {
          home: null, jump_clones: [], last_clone_jump_date: null, last_station_change_date: null,
          clone_jump_available_at: new Date(Date.now() + 5 * 3_600_000).toISOString(),
        },
      },
    }))
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Alice')
    expect(screen.getByText(/^1h 2\dm$|^1h 30m$|^1h 29m$/)).toBeInTheDocument()
    expect(screen.getByText('none')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Details' })[0])
    expect(await screen.findByText(/^4h 5\dm$|^5h 0m$/)).toBeInTheDocument()
  })
})
