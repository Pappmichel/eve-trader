import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { updatesApi } from '../api/client'
import { acceptOwnDataChange, DATA_VERSIONS_KEY, resetDataVersions } from '../dataVersions'
import { NewDataNotice } from './NewDataNotice'

vi.mock('../api/client', () => ({ updatesApi: { versions: vi.fn() } }))

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MantineProvider><NewDataNotice /></MantineProvider>
    </QueryClientProvider>,
  )
  return client
}

const V1 = { esi: '2026-10-03 10:00', shortlist: 'a', portfolio: null }

describe('NewDataNotice', () => {
  beforeEach(() => {
    resetDataVersions()
    vi.mocked(updatesApi.versions).mockReset()
  })

  it('stays hidden until a version changes, then refreshes everything on click', async () => {
    vi.mocked(updatesApi.versions).mockResolvedValue(V1)
    const client = setup()
    await waitFor(() => expect(client.getQueryData(DATA_VERSIONS_KEY)).toEqual(V1))
    expect(screen.queryAllByRole('button', { name: /New data/ })).toHaveLength(0)

    vi.mocked(updatesApi.versions).mockResolvedValue({ ...V1, esi: '2026-10-03 10:05' })
    await act(() => client.invalidateQueries({ queryKey: DATA_VERSIONS_KEY }))
    // Desktop button and mobile icon are both in the DOM (jsdom has no media queries).
    const [button] = await screen.findAllByRole('button', { name: /New data/ })

    const spy = vi.spyOn(client, 'invalidateQueries')
    await userEvent.click(button)
    expect(spy).toHaveBeenCalledWith()
    await waitFor(() => expect(screen.queryAllByRole('button', { name: /New data/ })).toHaveLength(0))
  })

  it("does not flag the user's own change", async () => {
    vi.mocked(updatesApi.versions).mockResolvedValue(V1)
    const client = setup()
    await waitFor(() => expect(client.getQueryData(DATA_VERSIONS_KEY)).toEqual(V1))

    vi.mocked(updatesApi.versions).mockResolvedValue({ ...V1, shortlist: 'b' })
    await act(async () => acceptOwnDataChange(client))
    await waitFor(() => expect(updatesApi.versions).toHaveBeenCalledTimes(2))
    expect(screen.queryAllByRole('button', { name: /New data/ })).toHaveLength(0)
  })

  it('ignores a source that has no value yet', async () => {
    vi.mocked(updatesApi.versions).mockResolvedValue(V1)
    const client = setup()
    await waitFor(() => expect(client.getQueryData(DATA_VERSIONS_KEY)).toEqual(V1))
    vi.mocked(updatesApi.versions).mockResolvedValue({ ...V1, portfolio: null })
    await act(() => client.invalidateQueries({ queryKey: DATA_VERSIONS_KEY }))
    expect(screen.queryAllByRole('button', { name: /New data/ })).toHaveLength(0)
  })
})
