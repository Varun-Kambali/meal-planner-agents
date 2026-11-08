/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dev server sends API calls to `make run-api` on this machine, so the browser sees one
// origin and the API needs no CORS setting locally. A build for another host sets VITE_API_BASE.
const API = 'http://127.0.0.1:8000'

export default defineConfig({
  // Where the built site is served from: `/` by default, `/<repository>/` on GitHub Pages.
  base: process.env.VITE_BASE_PATH || '/',
  plugins: [react()],
  build: {
    // Vite inlines small files as data: URLs, which a Content-Security-Policy that allows fonts
    // only from the page's own origin refuses. Fonts stay files.
    assetsInlineLimit: (file) => (file.endsWith('.woff2') ? false : undefined),
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/v1': API, '/healthz': API },
    // The tests read the server's plan files (tests/fixtures/plans) to check that they load.
    fs: { allow: ['.', '../tests/fixtures'] },
  },
  test: {
    environment: 'jsdom',
    restoreMocks: true,
    unstubGlobals: true,
    setupFiles: ['./src/test/setup.ts'],
    coverage: {
      provider: 'v8',
      // The core logic: what the app keeps, checks, works out and asks the API. Each file has
      // to reach the thresholds on its own, so a well-covered file cannot cover for another.
      include: [
        'src/storage.ts',
        'src/validate.ts',
        'src/mealLog.ts',
        'src/swap.ts',
        'src/planForm.ts',
        'src/api.ts',
      ],
      reporter: ['text'],
      thresholds: { perFile: true, statements: 80, branches: 80, functions: 80, lines: 80 },
    },
  },
})
