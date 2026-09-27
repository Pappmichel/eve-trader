import { beforeEach, describe, expect, it } from 'vitest'
import { clearChunkReloadAttemptFlag, isChunkLoadError, shouldAutoReloadForChunkError } from './chunkReload'

describe('isChunkLoadError', () => {
  it('matches the Chromium/Vite "failed to fetch" message', () => {
    expect(isChunkLoadError(new Error(
      'Failed to fetch dynamically imported module: https://evetrader.duckdns.org/assets/TradingLayout-Db37qXX9.js',
    ))).toBe(true)
  })

  it('matches the Firefox "error loading" message', () => {
    expect(isChunkLoadError(new Error('error loading dynamically imported module'))).toBe(true)
  })

  it('matches the Safari "module script" message', () => {
    expect(isChunkLoadError(new Error('Importing a module script failed'))).toBe(true)
  })

  it('does not match an unrelated render error', () => {
    expect(isChunkLoadError(new Error("Cannot read properties of undefined (reading 'foo')"))).toBe(false)
  })
})

describe('shouldAutoReloadForChunkError', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('allows the first attempt in a tab session', () => {
    expect(shouldAutoReloadForChunkError()).toBe(true)
  })

  it('blocks a second attempt without an intervening clear', () => {
    expect(shouldAutoReloadForChunkError()).toBe(true)
    expect(shouldAutoReloadForChunkError()).toBe(false)
  })

  it('allows another attempt after clearChunkReloadAttemptFlag', () => {
    expect(shouldAutoReloadForChunkError()).toBe(true)
    clearChunkReloadAttemptFlag()
    expect(shouldAutoReloadForChunkError()).toBe(true)
  })
})
