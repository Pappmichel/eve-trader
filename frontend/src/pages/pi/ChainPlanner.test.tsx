import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ChainPlanResultView } from './ChainPlanner'
import type { PiChainPlanResult } from '../../api/types'

const RESULT: PiChainPlanResult = {
  status: 'time_limit', mode: 'target', target_type_id: 1, target_name: 'Nano-Factory',
  target_units_per_day: 120, max_target_units_per_day: 150, profit_per_day: 5_000_000, profit_per_slot: 400_000,
  used_slots: 17, slots: 18, free_slots: 1, characters: 3,
  assignments: [{
    character_key: 'c1', character: 'Main', cc_level: 5, planet_id: 10, planet_name: 'Planet I', planet_type_id: 11,
    planet_type: 'Barren', chain: 'P0-P1', product_type_id: 2, product_name: 'Reactive Metals', is_extraction: false,
    units_per_day: 500, layout_request: {},
  }],
  stages: [{ type_id: 2, name: 'Reactive Metals', tier: 1, in_tree: true, colonies: 1, made: 500, needed: 500,
    internal: 400, bought: 0, sold: 100, discarded: 0 }],
  purchases: [{ type_id: 3, name: 'Silicon', units_per_day: 10, cost_per_day: 1000, reason_code: 'no_resource',
    reason: 'No planet in the system has this resource' }],
  notes: ['A note'], system: { name: '33-JRO' }, zone: 'nullsec', allow_buy: false,
}

describe('ChainPlanResultView', () => {
  it('renders status, colonies per character, stages and purchase reasons', () => {
    const client = new QueryClient()
    render(
      <QueryClientProvider client={client}>
        <MantineProvider><MemoryRouter><ChainPlanResultView result={RESULT} /></MemoryRouter></MantineProvider>
      </QueryClientProvider>,
    )
    expect(screen.getByText('Best plan found within the time limit')).toBeInTheDocument()
    expect(screen.getByText('Main')).toBeInTheDocument()
    expect(screen.getByText('Planet I (Barren)')).toBeInTheDocument()
    expect(screen.getByText('17 / 18')).toBeInTheDocument()
    expect(screen.getByText('No planet in the system has this resource')).toBeInTheDocument()
    expect(screen.getByText('A note')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Template' })).toBeInTheDocument()
  })
})
