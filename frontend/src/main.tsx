import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { ModalsProvider } from '@mantine/modals'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import '@mantine/core/styles.css'
import '@mantine/notifications/styles.css'
import '@mantine/spotlight/styles.css'
import './index.css'

import { theme } from './theme'
import App from './App'
import { errorsApi } from './api/client'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

// GitHub issue #88: ErrorBoundary.tsx only ever catches errors thrown
// *during React's own render* - an error thrown from an event handler, a
// setTimeout callback, or a rejected Promise never reaches it at all (React
// itself only re-throws render-phase errors). These two listeners are the
// only way to catch that other, larger class of runtime error - both
// best-effort (never block/interfere with whatever else the page is doing).
window.addEventListener('error', (event) => {
  errorsApi.report('frontend-uncaught', event.message, event.error?.stack, window.location.pathname).catch(() => {})
})
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason
  const message = reason instanceof Error ? reason.message : String(reason)
  const detail = reason instanceof Error ? reason.stack : undefined
  errorsApi.report('frontend-unhandled-rejection', message, detail, window.location.pathname).catch(() => {})
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark" forceColorScheme="dark">
      <Notifications position="top-right" />
      <ModalsProvider>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </ModalsProvider>
    </MantineProvider>
  </StrictMode>,
)
