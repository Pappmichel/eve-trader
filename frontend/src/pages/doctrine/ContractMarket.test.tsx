import { fireEvent, render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ContractMarket from './ContractMarket'
import { doctrineApi, sdeApi } from '../../api/client'

vi.mock('../../api/client', () => ({
  doctrineApi: {
    contractMarket: vi.fn(),
    settings: vi.fn(),
    scanContractMarket: vi.fn(),
    scanContractMarketStatus: vi.fn(),
  },
  sdeApi: { solarSystems: vi.fn() },
}))

vi.mock('../../hooks/useBackgroundJob', () => ({
  useBackgroundJob: () => ({
    status: null, runningStatus: null,
    formatProgress: () => '',
    jobName: 'contract_market_scan',
  }),
  useBackgroundJobStart: () => ({ mutate: vi.fn(), isPending: false }),
}))

const SETTINGS = {
  contract_market_system_id: 30000142,
  contract_market_include_public: true,
  contract_market_include_alliance: true,
}

const FITTING = {
  fitting_id: 'f1', fitting_name: 'Rifter Fleet', hull_type_id: 587, hull_name: 'Rifter',
  count: 0, min_price: null, avg_price: null, contracts: [],
}

function renderPage(payload: object) {
  vi.mocked(doctrineApi.contractMarket).mockResolvedValue(payload as never)
  vi.mocked(doctrineApi.settings).mockResolvedValue(SETTINGS as never)
  vi.mocked(sdeApi.solarSystems).mockResolvedValue([
    { solar_system_id: 30000142, solar_system_name: 'Jita' },
  ] as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MantineProvider><MemoryRouter><ContractMarket /></MemoryRouter></MantineProvider>
    </QueryClientProvider>,
  )
}

async function expandDoctrine(name: string) {
  fireEvent.click(await screen.findByText(name))
}

describe('Doctrine ContractMarket badges', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows gray Not scanned before the first scan', async () => {
    renderPage({
      scan: null,
      settings: SETTINGS,
      doctrines: [{ doctrine_id: 'd1', doctrine_name: 'Frigates', fittings: [FITTING] }],
    })
    expect(await screen.findByText('No scan yet. Pick a system and click Scan.')).toBeTruthy()
    expect(screen.getByText('Not scanned')).toBeTruthy()
    await expandDoctrine('Frigates')
    expect(await screen.findAllByText('Not scanned')).toHaveLength(2)
  })

  it('shows red 0 contracts after a scan with no hits', async () => {
    renderPage({
      scan: {
        scanned_at: '2026-01-01T00:00:00Z', system_id: 30000142, system_name: 'Jita',
        include_public: true, include_alliance: true, public_candidates: 0,
        alliance_candidates: 0, matched: 0, unknown_locations: 0, items_pending: 0,
        items_failed: 0, asks_for_items: 0,
      },
      settings: SETTINGS,
      doctrines: [{ doctrine_id: 'd1', doctrine_name: 'Frigates', fittings: [FITTING] }],
    })
    expect(await screen.findByText('0 contracts')).toBeTruthy()
    await expandDoctrine('Frigates')
    expect(await screen.findAllByText('0 contracts')).toHaveLength(2)
    expect(screen.queryByText('Not scanned')).toBeNull()
    fireEvent.click(await screen.findByText('Rifter Fleet'))
    expect(await screen.findByText('No contracts for this fitting in the last scan.')).toBeTruthy()
  })

  it('lists a hit count when contracts matched', async () => {
    renderPage({
      scan: {
        scanned_at: '2026-01-01T00:00:00Z', system_id: 30000142, system_name: 'Jita',
        include_public: true, include_alliance: true, public_candidates: 1,
        alliance_candidates: 0, matched: 1, unknown_locations: 0, items_pending: 0,
        items_failed: 0, asks_for_items: 0,
      },
      settings: SETTINGS,
      doctrines: [{
        doctrine_id: 'd1', doctrine_name: 'Frigates',
        fittings: [{
          ...FITTING, count: 2, min_price: 10_000_000, avg_price: 12_000_000,
          contracts: [{
            contract_id: 1, source: 'public', issuer_id: 1, issuer_name: 'Seller',
            start_location_id: 60003760, location_name: 'Jita IV', status: 'outstanding',
            title: 'Fit', price: 10_000_000, date_issued: null, date_expired: '2099-01-01T00:00:00Z',
            match_score: 1, validation_status: 'valid',
          }],
        }],
      }],
    })
    expect(await screen.findByText('2 contracts')).toBeTruthy()
    await expandDoctrine('Frigates')
    expect(await screen.findByText(/2 · min/)).toBeTruthy()
  })
})
