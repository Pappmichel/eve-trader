import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../notify', () => ({ notify: vi.fn() }))

import { notify } from '../notify'
import { notifyIfUnchanged } from './useAction'

describe('notifyIfUnchanged', () => {
  beforeEach(() => vi.mocked(notify).mockClear())

  it('shows the skipped-sync message of a manual sync result', () => {
    notifyIfUnchanged({ ok: true, unchanged_message: '2 data sets unchanged until 14:32 UTC (ESI serves the cached copy until then)' })
    expect(notify).toHaveBeenCalledTimes(1)
    expect(vi.mocked(notify).mock.calls[0][0].message).toContain('unchanged until 14:32 UTC')
  })

  it('stays silent without a message', () => {
    notifyIfUnchanged({ ok: true })
    notifyIfUnchanged({ unchanged_message: null })
    notifyIfUnchanged('OK')
    notifyIfUnchanged(undefined)
    expect(notify).not.toHaveBeenCalled()
  })
})
