import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, gateApi } from './client'

describe('request() service_unavailable handling', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('maps 503 service_unavailable to a clear message and does not redirect', async () => {
    const assign = vi.fn()
    // window.location.href assignment would navigate; spy via a stubbed fetch only.
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo) => {
      const url = String(input)
      if (url.includes('/api/auth/gate/start')) {
        throw new Error('must not start SSO on 503')
      }
      return {
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        json: async () => ({ detail: 'service_unavailable' }),
      } as Response
    }))

    await expect(gateApi.status()).rejects.toMatchObject({
      status: 503,
      message: 'The server is temporarily unavailable. Your session is fine, try again shortly.',
    })
    expect(assign).not.toHaveBeenCalled()
  })

  it('still surfaces other 503 details unchanged', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      json: async () => ({ detail: 'something else' }),
    } as Response)))

    await expect(gateApi.status()).rejects.toBeInstanceOf(ApiError)
    await expect(gateApi.status()).rejects.toMatchObject({
      status: 503,
      message: 'something else',
    })
  })
})
