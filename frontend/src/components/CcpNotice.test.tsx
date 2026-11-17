import { MantineProvider } from '@mantine/core'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import CcpNotice, { CCP_NOTICE_TEXT } from './CcpNotice'
import PiLayout from '../pages/pi/PiLayout'

vi.mock('./ToolHeader', () => ({ ToolHeader: () => <div>header</div> }))

function wrap(node: React.ReactNode) {
  const client = new QueryClient()
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>{node}</MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )
}

describe('CcpNotice', () => {
  it('renders the shared CCP text', () => {
    wrap(<CcpNotice />)
    expect(screen.getByTestId('ccp-notice')).toHaveTextContent(CCP_NOTICE_TEXT)
    expect(CCP_NOTICE_TEXT).toContain('registered trademarks of CCP hf')
    expect(CCP_NOTICE_TEXT).toContain('not affiliated with or endorsed by CCP hf')
  })

  it('is shown inside a tool layout', () => {
    wrap(<PiLayout />)
    expect(screen.getByTestId('ccp-notice')).toHaveTextContent(CCP_NOTICE_TEXT)
  })
})
