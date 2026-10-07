import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Colonies from './Colonies'

const COLONIES = {
  shared: true,
  now: '2026-10-07T12:00:00+00:00',
  characters: [
    {
      character_id: 1, character_name: 'Alice', state: 'ok', synced_at: '2026-10-07T11:00:00+00:00',
      colonies: [{
        planet_id: 40000001, planet_name: 'Jita IV', planet_type: 'barren', zone: 'highsec',
        upgrade_level: 5, radius_km: 5000, last_update: '2026-10-07T10:00:00+00:00', template_available: true,
        projection: {
          age_hours: 2, uncertain: false,
          extractors: [{
            pin_id: 3, product_type_id: 2272, product_name: 'Microorganisms', heads: 2,
            hours_left: 10, expired: false, per_head_per_hour: 400, rate_source: 'esi',
          }],
          storage_m3: 10000, stored_m3: 10, hours_until_full: null, hours_until_inputs_empty: null,
          inputs_empty_type: null, idle_factories: [], product_name: 'Bacteria', chain: 'P0-P1',
          skipped_routes: 0, cc_bypassed: 1,
        },
      }],
    },
    {
      character_id: 2, character_name: 'Bob', state: 'not_shared', synced_at: null, colonies: [],
    },
  ],
}

const CALIBRATION = {
  min_samples: 3, sample_count: 1,
  zones: [{ zone: 'highsec', count: 1, median: 900, default: 1000, active: false }],
  p0: [],
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider><MemoryRouter><Colonies /></MemoryRouter></MantineProvider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('PI Colonies', () => {
  it('lists a synced colony, a character that does not share, and the geometry caveat', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const body = String(url).includes('calibration') ? CALIBRATION : COLONIES
      return new Response(JSON.stringify(body), { status: 200 })
    }))
    renderPage()
    expect(await screen.findByText('Jita IV')).toBeInTheDocument()
    expect(screen.getByText((_, el) => el?.textContent === 'Microorganisms · 2 heads · 10 h · esi')).toBeInTheDocument()
    expect(screen.getByText(/Command center bypassed on 1 route/)).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
    expect(screen.getByText(/Share Planetary Industry with this tool/)).toBeInTheDocument()
    expect(screen.getByText(/Pin positions are not checked in game/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sync from EVE' })).toBeInTheDocument()
    expect(screen.getAllByText('highsec').length).toBeGreaterThan(0)
  })
})
