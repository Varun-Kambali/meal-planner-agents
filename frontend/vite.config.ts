/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dev server sends API calls to `make run-api` on this machine, so the browser sees one
// origin and the API needs no CORS setting locally. A build for another host sets VITE_API_BASE.
const API = 'http://127.0.0.1:8000'

export default defineConfig({
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
  },
  test: {
    environment: 'jsdom',
    restoreMocks: true,
    unstubGlobals: true,
  },
})
