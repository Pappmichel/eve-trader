import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import { productionApi } from '../../api/client'
import type { ReactionCapacityBlock } from '../../api/types'
import ReactionCapacityPanel, { CapacityReasonBadge } from './ReactionCapacityPanel'

vi.mock('../../api/client', () => ({
  productionApi: {
    itemNameOptions: vi.fn(),
    setReactionChainChoice: vi.fn(),
  },
}))

const BLOCK: ReactionCapacityBlock = {
  slots_used: 12,
  slots_available: 20,
  total_slots: 22,
  busy_slots: 2,
  window_next: '2026-11-03T17:00:00.000Z',
  w1_cost: 1e9,
  w2_preview_cost: 8e8,
  slot_shadow_price: 5e6,
  formula_shadow_prices: { '16670': 1e7 },
  chaining: [{
    product_type_id: 16670,
    buy_now_cost: 100,
    wait_cost: 80,
    isk_saved_by_waiting: 20,
    units_delayed: 50,
    preferred: 'buy',
    short_input_ids: [],
  }],
  warnings: [],
  recipes: [],
  buy: {},
  deferred: {},
  next_window: {
    jobs_used: 5,
    buy: {},
    recipes: [{
      recipe_key: 'normal:16670',
      product_type_id: 16670,
      jobs: 2,
      runs: 10,
      runs_per_job: 5,
      formulas_to_buy: 0,
    }],
  },
  component_slot_days: 40.5,
  cycle_days: 13,
}

function renderPanel(block: ReactionCapacityBlock = BLOCK) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider>
        <ReactionCapacityPanel block={block} />
      </MantineProvider>
    </QueryClientProvider>,
  )
}

describe('ReactionCapacityPanel', () => {
  it('shows the header line with slots and window', async () => {
    vi.mocked(productionApi.itemNameOptions).mockResolvedValue([
      { type_id: 16670, type_name: 'Sylramic Fibers' },
    ])
    renderPanel()
    expect(screen.getByText(/Reactions: 12 of 20 jobs/)).toBeInTheDocument()
    expect(screen.getByText(/Component slot-days \/ cycle: 40\.5/)).toBeInTheDocument()
    expect(screen.getByText(/Formulas worth buying: 1/)).toBeInTheDocument()
  })

  it('lets the user pick wait on a chaining row', async () => {
    vi.mocked(productionApi.itemNameOptions).mockResolvedValue([
      { type_id: 16670, type_name: 'Sylramic Fibers' },
    ])
    vi.mocked(productionApi.setReactionChainChoice).mockResolvedValue({
      type_id: 16670, choice: 'wait',
    })
    const user = userEvent.setup()
    renderPanel()

    expect(await screen.findByText('Sylramic Fibers')).toBeInTheDocument()
    const control = screen.getByTestId('reaction-chain-choice-16670')
    await user.click(control.querySelector('input[value="wait"]') ?? control)
    // SegmentedControl may need clicking the Wait label.
    const waitLabel = screen.getByText('Wait')
    await user.click(waitLabel)

    await waitFor(() => {
      expect(productionApi.setReactionChainChoice).toHaveBeenCalledWith(16670, 'wait')
    })
  })

  it('expands the next-window forecast', async () => {
    vi.mocked(productionApi.itemNameOptions).mockResolvedValue([])
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByText(/Next-window preview/))
    expect(screen.getByText(/W2 jobs used: 5/)).toBeInTheDocument()
    expect(screen.getByText(/normal:16670/)).toBeInTheDocument()
  })
})

describe('CapacityReasonBadge', () => {
  it('renders capacity and formula badges', () => {
    const { rerender } = render(
      <MantineProvider>
        <CapacityReasonBadge reason="capacity" iskGivenUp={1_500_000} />
      </MantineProvider>,
    )
    expect(screen.getByText(/capacity/)).toBeInTheDocument()
    rerender(
      <MantineProvider>
        <CapacityReasonBadge reason="formula" iskGivenUp={2e6} />
      </MantineProvider>,
    )
    expect(screen.getByText('formula')).toBeInTheDocument()
  })
})
