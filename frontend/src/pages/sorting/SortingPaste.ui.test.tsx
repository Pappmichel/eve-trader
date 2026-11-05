import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { sortingApi } from '../../api/client'
import SortingPaste from './SortingPaste'

vi.mock('../../api/client', () => ({
  sortingApi: {
    sortingPaste: vi.fn(),
  },
}))

describe('SortingPaste', () => {
  it('sends the paste and shows the rows and the lines it could not read', async () => {
    vi.mocked(sortingApi.sortingPaste).mockResolvedValue({
      rows: [{
        type_id: 36, type_name: 'Mexallon', intake_qty: 500,
        by_source: [{ source_label: 'Pasted', qty: 500 }],
        wanted_by_tool: [{ tool: 'material', wanted_qty: 100 }],
        unclaimed: false,
      }],
      reprocess_error: null,
      unresolved: [{ line: 'Tritanum\t5', suggestion: 'Tritanium' }],
      errors: [{ line: 'free text', error: 'Not tab-separated' }],
    })

    const user = userEvent.setup()
    render(
      <MantineProvider>
        <QueryClientProvider client={new QueryClient()}>
          <SortingPaste />
        </QueryClientProvider>
      </MantineProvider>,
    )

    const button = screen.getByRole('button', { name: 'Sort paste' })
    expect(button).toBeDisabled()

    await user.type(screen.getByLabelText('Paste items here'), 'raw paste')
    await user.click(button)

    expect(vi.mocked(sortingApi.sortingPaste).mock.calls[0][0]).toBe('raw paste')
    expect(await screen.findByText('Mexallon')).toBeInTheDocument()
    expect(screen.getByText('Pasted: 500')).toBeInTheDocument()
    expect(screen.getByText(/1 line\(s\) could not be parsed: free text/)).toBeInTheDocument()
    expect(screen.getByText(/did you mean "Tritanium"\?/)).toBeInTheDocument()
  })
})
