import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// GitHub issue #85: first real, durable test coverage for the frontend -
// jsdom (not the default node environment) since DataTable.test.tsx mounts
// real components via @testing-library/react. globals stays off (test
// files import describe/it/expect explicitly from 'vitest') so the app's
// tsconfig doesn't need a "vitest/globals" types entry just to make
// `tsc -b` (part of `npm run build`) happy about test-only globals.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
  },
})
