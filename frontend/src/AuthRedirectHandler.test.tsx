import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const notify = vi.fn()
vi.mock('./notify', () => ({ notify: (args: unknown) => notify(args) }))

import { AuthRedirectHandler } from './AuthRedirectHandler'

function renderAt(url: string) {
  render(
    <MemoryRouter initialEntries={[url]}>
      <AuthRedirectHandler />
    </MemoryRouter>,
  )
}

describe('AuthRedirectHandler', () => {
  beforeEach(() => notify.mockClear())

  it('maps a known error code to fixed text', () => {
    renderAt('/?auth=error&message=nonce_mismatch')
    expect(notify.mock.calls[0][0].message).toMatch(/same browser|browser that started/)
  })

  it('never shows foreign message text', () => {
    renderAt('/?auth=error&message=' + encodeURIComponent('Call +1 555 to unlock your account'))
    const shown = notify.mock.calls[0][0].message as string
    expect(shown).toBe('Login failed. Try again.')
    expect(shown).not.toContain('555')
  })

  it('omits a character that does not look like a name and a malformed role', () => {
    renderAt('/?auth=success&role=evil%20text&character=' + encodeURIComponent('<b>Visit evil.example</b>'))
    const shown = notify.mock.calls[0][0].message as string
    expect(shown).not.toContain('evil')
    expect(shown).not.toContain('<b>')
  })

  it('shows a valid role and character', () => {
    renderAt('/?auth=success&role=esi:123&character=Some%20Pilot')
    expect(notify.mock.calls[0][0].message).toBe('esi:123: Some Pilot')
  })
})
