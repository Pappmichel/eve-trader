import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it } from 'vitest'

import type { RealizedChainNode, RealizedFlags } from '../../../api/types'
import { CostChainTree } from './CostChainTree'

function flags(partial: Partial<RealizedFlags> = {}): RealizedFlags {
  return {
    estimated: false, price_missing: false, assumed_me: false, assumed_decryptor: false,
    coverage: null, tax_missing: false, detail_missing: false, no_structure_bonus: null,
    ...partial,
  }
}

function node(partial: Partial<RealizedChainNode> & Pick<RealizedChainNode, 'kind'>): RealizedChainNode {
  return {
    origin: null, type_id: null, type_name: null, quantity: null, unit: null,
    unit_cost: null, unit_price: null, reference_price: null, date: null, end_date: null,
    location_id: null, freight: null, freight_label: null, freight_rate_per_m3: null,
    source_ref: null, job_id: null, job_ref: null, activity_id: null, runs: null, source: null,
    successful_runs: null, state: null, job_cost: null, facility_id: null,
    output_quantity: null, cost_total: null, share: null, cost_share: null, sale_ref: null,
    flags: flags(), children: [],
    ...partial,
  }
}

describe('CostChainTree', () => {
  it('shows the job share and styles opening and unknown inputs as estimates', () => {
    const tree = node({
      kind: 'product', type_name: 'Nanofiber II', quantity: 1, sale_ref: 'sale-1',
      children: [
        node({
          kind: 'job', origin: 'produced', type_name: 'Nanofiber II', job_id: 42,
          runs: 5, share: 0.5, cost_share: 80, job_cost: 20, quantity: 1,
          flags: flags({ assumed_me: true, detail_missing: false, no_structure_bonus: null }),
          children: [
            node({
              kind: 'purchase', origin: 'bought', type_name: 'Tritanium', quantity: 10,
              unit_cost: 6, unit_price: 5, freight: 10, freight_label: 'Configured rate (no courier yet)',
              date: '2026-10-01T00:00:00Z', location_id: 60003760, source: 'contract',
            }),
            node({
              kind: 'estimate', origin: 'opening', type_name: 'Pyerite', quantity: 4,
              unit_cost: 8, reference_price: 8, flags: flags({ estimated: true }),
            }),
            node({
              kind: 'estimate', origin: 'unknown', type_name: 'Mexallon', quantity: 2,
              unit_cost: 9, reference_price: 9, flags: flags({ estimated: true, assumed_decryptor: true }),
            }),
          ],
        }),
      ],
    })

    render(
      <MantineProvider>
        <CostChainTree node={tree} locationNames={{ '60003760': 'Jita 4-4' }} />
      </MantineProvider>,
    )

    expect(screen.getByText('Share 50%')).toBeInTheDocument()
    expect(screen.getByText('Cost share 80 ISK')).toBeInTheDocument()
    const share = screen.getByText('Share 50%')
    expect(share).toHaveAttribute('data-share', '0.5')

    const bought = screen.getByText('Tritanium')
    expect(bought).toHaveAttribute('data-estimate', 'false')
    expect(screen.getByText('Freight 10 ISK (Configured rate (no courier yet))')).toBeInTheDocument()
    expect(screen.getByText('Jita 4-4 (60003760)')).toBeInTheDocument()
    expect(screen.getByText('Price 5 ISK')).toBeInTheDocument()
    expect(screen.getByText('contract')).toBeInTheDocument()

    const opening = screen.getByText('Pyerite')
    expect(opening).toHaveAttribute('data-estimate', 'true')
    const unknown = screen.getByText('Mexallon')
    expect(unknown).toHaveAttribute('data-estimate', 'true')
    expect(screen.getAllByText('Valued at 8 ISK').length).toBeGreaterThan(0)

    expect(screen.getByText('assumed ME')).toBeInTheDocument()
    expect(screen.getByText('assumed decryptor')).toBeInTheDocument()
    expect(screen.getByText('structure bonus: unknown')).toBeInTheDocument()

    const direct = document.querySelectorAll('[data-depth="1"]')
    expect(direct).toHaveLength(1)
    expect(direct[0]).toHaveAttribute('data-line-cost', '80')
  })

  it('shows the courier id and rate on a bought draw', () => {
    const tree = node({
      kind: 'product', type_name: 'Widget', quantity: 1,
      children: [
        node({
          kind: 'purchase', origin: 'bought', type_name: 'Tritanium', quantity: 1,
          freight: 800, freight_label: 'Courier 236650353, 236650362 · 800 ISK/m3',
        }),
      ],
    })
    render(
      <MantineProvider>
        <CostChainTree node={tree} locationNames={{}} />
      </MantineProvider>,
    )
    expect(screen.getByText('Freight 800 ISK (Courier 236650353, 236650362 · 800 ISK/m3)')).toBeInTheDocument()
  })
})
