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
    setReactionChainChoice: vi.fn(),
  },
}))

const BLOCK: ReactionCapacityBlock = {
  slots_used: 12,
  slots_available: 20,
  total_slots: 22,
  busy_slots: 2,
  window_anchor: '2026-10-06T17:00:00.000Z',
  window_next: '2026-11-03T17:00:00.000Z',
  w1_cost: 1e9,
  w2_preview_cost: 8e8,
  slot_shadow_price: 5e6,
  formula_shadow_prices: { '16670': 1e7 },
  formulas_to_buy_count: 2,
  formulas_to_buy_isk: 20_000_000,
  chaining: [{
    product_type_id: 16670,
    type_name: 'Sylramic Fibers',
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
      type_name: 'Sylramic Fibers',
      recipe_kind: 'normal',
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
  it('shows the header line with slots, window, and formulas to buy', () => {
    renderPanel()
    expect(screen.getByText(/Reactions: 12 of 20 jobs/)).toBeInTheDocument()
    expect(screen.getByText(/Component slot-days \/ cycle: 40\.5/)).toBeInTheDocument()
    expect(screen.getByTestId('formulas-to-buy')).toHaveTextContent(/Formulas to buy: 2/)
  })

  it('lets the user pick wait on a chaining row using payload type_name', async () => {
    vi.mocked(productionApi.setReactionChainChoice).mockResolvedValue({
      type_id: 16670, choice: 'wait',
    })
    const user = userEvent.setup()
    renderPanel()

    expect(screen.getAllByText('Sylramic Fibers').length).toBeGreaterThanOrEqual(1)
    const waitLabel = screen.getByText('Wait')
    await user.click(waitLabel)

    await waitFor(() => {
      expect(productionApi.setReactionChainChoice).toHaveBeenCalledWith(16670, 'wait')
    })
  })

  it('expands the next-window forecast with product name and recipe kind', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByText(/Next-window preview/))
    expect(screen.getByText(/W2 jobs used: 5/)).toBeInTheDocument()
    // Product name appears in chaining + preview; recipe kind is the preview column.
    expect(screen.getAllByText('Sylramic Fibers').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('normal')).toBeInTheDocument()
    expect(screen.queryByText(/normal:16670/)).not.toBeInTheDocument()
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
