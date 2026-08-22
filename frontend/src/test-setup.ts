import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// globals is off (see vitest.config.ts) - @testing-library/react's own
// auto-cleanup-after-each-test only registers itself when globals are on,
// so without this every test in a file would render on top of the
// previous test's still-mounted DOM instead of a clean one.
afterEach(cleanup)

// jsdom has no ResizeObserver and reports every element as 0x0 - both fine
// for most components, but DataTable.tsx's @tanstack/react-virtual needs a
// non-zero scroll-container viewport to compute which rows are "visible"
// at all, or DataTable.test.tsx would see zero rendered rows regardless of
// data. A fixed-size stub is enough - the tests here care about which rows
// exist/their order, not real pixel layout.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!('ResizeObserver' in globalThis)) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(globalThis as any).ResizeObserver = ResizeObserverStub
}
Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, value: 600 })
Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, value: 800 })
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 600 })
Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 800 })
HTMLElement.prototype.getBoundingClientRect = function () {
  return { x: 0, y: 0, top: 0, left: 0, bottom: 600, right: 800, width: 800, height: 600, toJSON() {} }
}

// jsdom doesn't implement matchMedia at all - MantineProvider's own color
// scheme detection (auto light/dark) calls it unconditionally on mount, so
// every test rendering anything under MantineProvider needs this stubbed.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})
