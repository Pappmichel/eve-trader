import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { Notifications } from '@mantine/notifications'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { charactersApi } from '../../api/client'
import CharactersPage from './CharactersPage'

vi.mock('../../api/client', () => ({
  charactersApi: {
    owners: vi.fn(),
    sharing: vi.fn(),
    freshness: vi.fn(),
    capabilities: vi.fn(),
    setSharing: vi.fn(),
    setCapability: vi.fn(),
    accessPreview: vi.fn(),
    reauthStart: vi.fn(),
    addStart: vi.fn(),
    sync: vi.fn(),
    checkCorporationRoles: vi.fn(),
    removeCharacter: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  },
}))

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MantineProvider>
      <Notifications />
      <ModalsProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <CharactersPage />
          </MemoryRouter>
        </QueryClientProvider>
      </ModalsProvider>
    </MantineProvider>,
  )
}

describe('Characters page', () => {
  it('renders the four sections in order, the decision-4 sentence, five-state cells, a popover toggle, pending re-auth, and the tool view', async () => {
    vi.mocked(charactersApi.owners).mockResolvedValue([
      {
        character_id: 1,
        character_name: 'Alice',
        write_role: 'producer:1',
        character_has_token_pool: true,
        roles: ['producer:1', 'doctrine-assets:1'],
        corporation_id: 99,
        corporation_name: 'Test Corp',
      },
    ])
    vi.mocked(charactersApi.sharing).mockResolvedValue([
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'production' },
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'doctrine' },
      { owner_type: 'character', owner_id: 1, data_kind: 'wallet', tool_key: 'trading' },
      { owner_type: 'corporation', owner_id: 99, data_kind: 'wallet', tool_key: 'trading' },
    ])
    vi.mocked(charactersApi.freshness).mockResolvedValue([
      {
        owner_type: 'character', owner_id: 1, data_kind: 'industry_jobs',
        last_success_at: null, last_attempt_at: 't', last_error: null,
      },
    ])
    vi.mocked(charactersApi.capabilities).mockResolvedValue([])
    vi.mocked(charactersApi.accessPreview).mockResolvedValue({
      title: 'Confirm ESI access',
      items: [
        { key: 'assets', label: 'Assets', group: 1, added: false },
        { key: 'wallet', label: 'Wallet', group: 1, added: true },
      ],
    })
    vi.mocked(charactersApi.setSharing).mockResolvedValue({
      owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'sorting', enabled: true,
    })

    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'Characters' })).toBeInTheDocument()
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings).toEqual(['Characters', 'Corporations', 'Access', 'Tool view'])
    expect(screen.getByText(/Sharing governs raw ESI snapshots only/)).toBeInTheDocument()
    expect(screen.getByText(/Derived tables \(realized trades, shortlists, production plans\) are not filtered by it/)).toBeInTheDocument()
    expect((await screen.findAllByText('Alice')).length).toBeGreaterThan(0)
    expect(screen.getByText('token pool')).toBeInTheDocument()
    // Alice's access preview has wallet added:true (ticked, not yet on any
    // token) - the row-level badge surfaces that without opening a popover.
    expect(screen.getByText('re-auth needed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'assets 2/5' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'wallet re-auth' })).toBeInTheDocument()
    expect(screen.getByText('Test Corp')).toBeInTheDocument()
    expect(screen.queryByText('Corporation 99')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check corp roles' })).toBeInTheDocument()
    expect(screen.getByText('Structure name resolution')).toBeInTheDocument()
    expect(screen.getByText('Structure market book')).toBeInTheDocument()
    expect(screen.getByText('Sorting has no Assets source')).toBeInTheDocument()
    expect(screen.getByText('Trading has no Assets source')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add character' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sync everything' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Re-authorize' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'assets 2/5' }))
    expect(await screen.findByText('Toggling writes or deletes one sharing row. It does not call ESI.')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Production' })).toBeChecked()
    const sortingSwitch = screen.getByRole('switch', { name: 'Sorting' })
    expect(sortingSwitch).not.toBeChecked()
    await user.click(sortingSwitch)
    expect(vi.mocked(charactersApi.setSharing).mock.calls[0][0]).toEqual({
      owner_type: 'character',
      owner_id: 1,
      data_kind: 'assets',
      tool_key: 'sorting',
      enabled: true,
    })
  })

  it('shows a role-missing badge after Check corp roles reports has_role: false', async () => {
    vi.mocked(charactersApi.owners).mockResolvedValue([
      {
        character_id: 1, character_name: 'Alice', write_role: 'esi:1',
        character_has_token_pool: false, roles: ['esi:1'], corporation_id: 99,
        corporation_name: null,
      },
    ])
    vi.mocked(charactersApi.sharing).mockResolvedValue([
      { owner_type: 'corporation', owner_id: 99, data_kind: 'wallet', tool_key: 'trading' },
    ])
    vi.mocked(charactersApi.freshness).mockResolvedValue([])
    vi.mocked(charactersApi.capabilities).mockResolvedValue([])
    vi.mocked(charactersApi.checkCorporationRoles).mockResolvedValue({
      corporations: [{
        corporation_id: 99,
        checked_characters: ['Alice'],
        unchecked_characters: [],
        data_kinds: {
          assets: { required_roles: ['Director'], has_role: false },
          industry_jobs: { required_roles: ['Director'], has_role: false },
          blueprints: { required_roles: ['Director'], has_role: false },
          market_orders: { required_roles: ['Accountant', 'Trader'], has_role: false },
          wallet: { required_roles: ['Accountant', 'Junior_Accountant'], has_role: true },
        },
      }],
    })

    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('Corporation 99')).toBeInTheDocument()
    expect(screen.queryByText('role missing')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Check corp roles' }))

    expect(await screen.findAllByText('role missing')).toHaveLength(4)  // every kind but wallet
    expect(vi.mocked(charactersApi.checkCorporationRoles)).toHaveBeenCalledTimes(1)
  })

  it('offers Add character on the empty state and sends the browser to the SSO url', async () => {
    vi.mocked(charactersApi.owners).mockResolvedValue([])
    vi.mocked(charactersApi.sharing).mockResolvedValue([])
    vi.mocked(charactersApi.freshness).mockResolvedValue([])
    vi.mocked(charactersApi.capabilities).mockResolvedValue([])
    vi.mocked(charactersApi.addStart).mockResolvedValue({ url: 'https://login.eveonline.com/v2/oauth/authorize?x=1' })

    // jsdom's own window.location is not assignable; the page does a plain
    // `window.location.href = url` (same as Re-authorize), so stub it.
    const assigned: string[] = []
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { get href() { return '' }, set href(v: string) { assigned.push(v) } },
    })

    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText(/No ESI characters registered yet/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add character' }))

    await vi.waitFor(() => {
      expect(assigned).toEqual(['https://login.eveonline.com/v2/oauth/authorize?x=1'])
    })
    expect(vi.mocked(charactersApi.addStart)).toHaveBeenCalledTimes(1)
  })

  it('shows shared tools and access capabilities in the Remove confirm dialog, then deletes the character', async () => {
    vi.mocked(charactersApi.owners).mockResolvedValue([
      {
        character_id: 1,
        character_name: 'Alice',
        write_role: 'buyer:1',
        character_has_token_pool: false,
        roles: ['buyer:1'],
        corporation_id: 99,
        corporation_name: 'Test Corp',
      },
    ])
    vi.mocked(charactersApi.sharing).mockResolvedValue([
      { owner_type: 'character', owner_id: 1, data_kind: 'assets', tool_key: 'production' },
      { owner_type: 'character', owner_id: 1, data_kind: 'wallet', tool_key: 'trading' },
      { owner_type: 'corporation', owner_id: 99, data_kind: 'wallet', tool_key: 'trading' },
    ])
    vi.mocked(charactersApi.freshness).mockResolvedValue([])
    vi.mocked(charactersApi.capabilities).mockResolvedValue([
      { character_id: 1, capability_key: 'structure_market_book' },
    ])
    vi.mocked(charactersApi.removeCharacter).mockResolvedValue({
      removed: 1,
      character_name: 'Alice',
      roles: ['buyer:1'],
      shared_tools: ['production', 'trading'],
      capabilities: ['structure_market_book'],
      mail_archive_deleted: null,
    })

    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByRole('button', { name: 'Remove' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Remove Alice/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Shared with: Trading, Production/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Access: Structure market book/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Sharing ticks and Access capabilities stay/)).toBeInTheDocument()

    // R8: the archived mail goes along only when ticked.
    await user.click(within(dialog).getByRole('checkbox', { name: /archived mail/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))
    await vi.waitFor(() => {
      expect(vi.mocked(charactersApi.removeCharacter).mock.calls[0][0])
        .toEqual({ characterId: 1, deleteMailArchive: true })
    })
  })

  it('groups the sharing table into collapsible sections and marks live-only kinds', async () => {
    vi.mocked(charactersApi.owners).mockResolvedValue([
      {
        character_id: 1, character_name: 'Alice', write_role: 'esi:1',
        character_has_token_pool: false, roles: ['esi:1'], corporation_id: 99,
        corporation_name: 'Test Corp',
      },
    ])
    vi.mocked(charactersApi.sharing).mockResolvedValue([
      { owner_type: 'character', owner_id: 1, data_kind: 'location', tool_key: 'char_info' },
      { owner_type: 'character', owner_id: 1, data_kind: 'standings', tool_key: 'char_info' },
    ])
    vi.mocked(charactersApi.freshness).mockResolvedValue([])
    vi.mocked(charactersApi.capabilities).mockResolvedValue([])
    vi.mocked(charactersApi.accessPreview).mockResolvedValue({ title: 'Confirm ESI access', items: [] })

    const user = userEvent.setup()
    renderPage()

    await screen.findAllByText('Alice')
    // Both section headers are there, every kind is a column while expanded.
    expect(screen.getByRole('button', { name: 'Collapse Industry & Trading' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Loyalty Points' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Online Status' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'location all tools' })).toBeInTheDocument()

    // Collapsing the Character section swaps its fourteen columns for one summary.
    await user.click(screen.getByRole('button', { name: 'Collapse Character' }))
    expect(screen.queryByRole('columnheader', { name: 'Loyalty Points' })).not.toBeInTheDocument()
    expect(screen.getByText('2/14 shared')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Assets' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Expand Character' }))
    // Live-only kinds say so in their popover, snapshot kinds do not.
    await user.click(screen.getByRole('button', { name: 'location all tools' }))
    expect(await screen.findByText(/Read live from ESI while Character Info is open/)).toBeInTheDocument()
  })
})
