import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { sortingApi } from '../../api/client'
import SortingOverview from './SortingOverview'

vi.mock('../../api/client', () => ({
  sortingApi: {
    sortingList: vi.fn(),
  },
}))

function renderOverview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <SortingOverview />
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('SortingOverview table chrome', () => {
  it('exposes DataTable sort/filter/export and page-level tool/source filters', async () => {
    vi.mocked(sortingApi.sortingList).mockResolvedValue({
      rows: [
        {
          type_id: 36, type_name: 'Mexallon', intake_qty: 500,
          by_source: [{ source_label: 'Intake', qty: 500 }],
          wanted_by_tool: [{ tool: 'material', wanted_qty: 100 }],
          unclaimed: false,
        },
        {
          type_id: 34, type_name: 'Tritanium', intake_qty: 40,
          by_source: [{ source_label: 'Alice (Hangar)', qty: 40 }],
          wanted_by_tool: [],
          unclaimed: true,
        },
      ],
    })

    const user = userEvent.setup()
    renderOverview()

    expect(await screen.findByText('Mexallon')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Filter...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Columns' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Item/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Wanted by/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /By source/ })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Wanted by' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Source' })).toBeInTheDocument()
    expect(screen.getByText('2 of 2 items')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Filter...'), 'mex')
    const body = screen.getAllByRole('row').slice(1)
    expect(body.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual(['Mexallon'])
  })
})
