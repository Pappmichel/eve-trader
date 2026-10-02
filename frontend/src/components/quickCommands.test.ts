import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../notify', () => ({ notify: vi.fn() }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  tradingApi: {
    refreshShortlist: vi.fn(), startRefreshAndPrune: vi.fn(), reconcileTrades: vi.fn(), runPipeline: vi.fn(),
  },
}))

import { ApiError } from '../api/client'
import { notify } from '../notify'
import { QUICK_COMMANDS, executeQuickCommand, visibleCommands } from './quickCommands'

const deps = () => ({ invalidate: vi.fn(), navigate: vi.fn() })

describe('visibleCommands', () => {
  it('shows everything while grants are not loaded', () => {
    expect(visibleCommands(undefined)).toHaveLength(QUICK_COMMANDS.length)
  })
  it('only offers the commands of tools the character holds', () => {
    expect(visibleCommands([])).toHaveLength(0)
    expect(visibleCommands(['trading']).every((c) => c.toolKey === 'trading')).toBe(true)
    expect(visibleCommands(['production']).map((c) => c.id)).toEqual(['cmd-production-sync', 'cmd-production-refresh'])
    expect(visibleCommands(['doctrine', 'station_trading']).map((c) => c.toolKey))
      .toEqual(['doctrine', 'doctrine', 'station_trading'])
  })
  it('covers every tool that has live refresh buttons, each with a route and queries to refresh', () => {
    expect([...new Set(QUICK_COMMANDS.map((c) => c.toolKey))].sort())
      .toEqual(['doctrine', 'module_reprocessing', 'production', 'refining', 'station_trading', 'trading'])
    for (const c of QUICK_COMMANDS) {
      expect(c.goto.startsWith('/')).toBe(true)
      expect(c.invalidate.length).toBeGreaterThan(0)
      expect(c.effect.length).toBeGreaterThan(10)
    }
  })
})

describe('executeQuickCommand', () => {
  beforeEach(() => vi.mocked(notify).mockClear())

  it('runs the command, invalidates its queries and navigates', async () => {
    const cmd = { ...QUICK_COMMANDS[0], run: vi.fn().mockResolvedValue({}) }
    const d = deps()
    await executeQuickCommand(cmd, d)
    expect(cmd.run).toHaveBeenCalled()
    expect(d.invalidate).toHaveBeenCalledWith(['trading', 'pipeline', 'refresh-and-prune'])
    expect(d.navigate).toHaveBeenCalledWith('/trading/shortlist')
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ message: 'Started' }))
  })

  it('reports a 409 as "already running" and still opens the page', async () => {
    const cmd = { ...QUICK_COMMANDS[0], run: vi.fn().mockRejectedValue(new ApiError(409, 'busy')) }
    const d = deps()
    await executeQuickCommand(cmd, d)
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ title: 'Job already running', color: 'warn' }))
    expect(d.navigate).toHaveBeenCalledWith('/trading/shortlist')
    expect(d.invalidate).not.toHaveBeenCalled()
  })

  it('reports other errors without navigating', async () => {
    const cmd = { ...QUICK_COMMANDS[2], run: vi.fn().mockRejectedValue(new ApiError(500, 'boom')) }
    const d = deps()
    await executeQuickCommand(cmd, d)
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ color: 'danger', message: 'boom' }))
    expect(d.navigate).not.toHaveBeenCalled()
  })
})
