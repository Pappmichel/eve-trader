import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ToolHeader } from './ToolHeader'

vi.mock('../api/client', () => ({ updatesApi: { versions: vi.fn().mockResolvedValue({}) } }))

function renderHeader(props: Partial<Parameters<typeof ToolHeader>[0]> = {}) {
  const onToggle = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MantineProvider>
      <MemoryRouter initialEntries={['/trading']}>
        <Routes>
          <Route path="/" element={<div>landing</div>} />
          <Route path="/trading" element={<ToolHeader title="Trading" opened={false} onToggle={onToggle} {...props} />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>
    </QueryClientProvider>,
  )
  return onToggle
}

describe('ToolHeader', () => {
  it('shows the tool name and wires the burger and the way back to the tool picker', async () => {
    const onToggle = renderHeader()
    const user = userEvent.setup()
    expect(screen.getByText('Trading')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Toggle navigation' }))
    expect(onToggle).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Back to tools' }))
    expect(screen.getByText('landing')).toBeInTheDocument()
  })

  it('offers the page jump only when asked, as an icon button with a label for phones', () => {
    renderHeader()
    expect(screen.queryByRole('button', { name: 'Jump to a page' })).not.toBeInTheDocument()
  })

  it('has a labelled jump button for tools that support it', () => {
    renderHeader({ showJump: true })
    expect(screen.getByRole('button', { name: 'Jump to a page' })).toBeInTheDocument()
  })
})
