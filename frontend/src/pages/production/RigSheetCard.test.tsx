import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { describe, expect, it, vi } from 'vitest'

import type { RigSheetStatus } from '../../api/types'
import { RigSheetCard, rigSheetIsStale } from './RigSheetCard'

const NOW = Date.parse('2026-11-17T12:00:00Z')
const status = (extra: Partial<RigSheetStatus> = {}): RigSheetStatus => ({
  configured: true, fetched_at: '2026-11-17T09:00:00Z', age_hours: 3, system_count: 12, duplicates: [],
  error: null, error_at: null, ...extra,
})

function renderCard(s: RigSheetStatus | undefined, onReload = vi.fn()) {
  render(<MantineProvider><RigSheetCard status={s} reloading={false} onReload={onReload} /></MantineProvider>)
  return onReload
}

describe('rigSheetIsStale', () => {
  it('is false without a link or a status', () => {
    expect(rigSheetIsStale(undefined, NOW)).toBe(false)
    expect(rigSheetIsStale(status({ configured: false }), NOW)).toBe(false)
  })
  it('is true only once the stored copy is older than 24 hours', () => {
    expect(rigSheetIsStale(status({ age_hours: 23 }), NOW)).toBe(false)
    expect(rigSheetIsStale(status({ age_hours: 25 }), NOW)).toBe(true)
  })
  it('loads a never-fetched sheet once, but does not retry a failing link on its own', () => {
    expect(rigSheetIsStale(status({ fetched_at: null, age_hours: null }), NOW)).toBe(true)
    expect(rigSheetIsStale(status({ fetched_at: null, age_hours: null, error: 'x', error_at: '2026-11-10T00:00:00Z' }), NOW)).toBe(false)
  })
  it('waits an hour after a failed reload of an old copy', () => {
    expect(rigSheetIsStale(status({ age_hours: 30, error: 'x', error_at: '2026-11-17T11:30:00Z' }), NOW)).toBe(false)
    expect(rigSheetIsStale(status({ age_hours: 30, error: 'x', error_at: '2026-11-17T09:00:00Z' }), NOW)).toBe(true)
  })
})

describe('RigSheetCard', () => {
  it('renders nothing without a link', () => {
    renderCard(status({ configured: false }))
    expect(screen.queryByText('Rig sheet')).toBeNull()
  })

  it('shows the age, the error and reloads on the button', async () => {
    const onReload = renderCard(status({ error: 'The rig sheet could not be fetched (HTTP 403).' }))
    expect(screen.getByText(/12 systems, loaded 3 h ago/)).toBeTruthy()
    expect(screen.getByText(/HTTP 403/)).toBeTruthy()
    expect(screen.getByText(/previous copy is still in use/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Reload rig sheet' }))
    expect(onReload).toHaveBeenCalledTimes(1)
  })

  it('reloads on its own when the stored copy is older than 24 hours', () => {
    const onReload = renderCard(status({ age_hours: 40, fetched_at: '2026-11-15T20:00:00Z' }))
    expect(onReload).toHaveBeenCalledTimes(1)
  })

  it('does not reload on its own while the copy is fresh', () => {
    const onReload = renderCard(status())
    expect(onReload).not.toHaveBeenCalled()
  })
})
