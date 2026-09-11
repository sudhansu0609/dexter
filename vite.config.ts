/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The only place in the repo that still carries a port number, and even here it
// is a default the environment overrides (GUARDIAN_PLAN.md section 11): the dev
// server needs *some* address to proxy to before the backend has published one.
// Launch with VITE_DEXTER_PORT=<the port Dexter reported> to follow it.
const backendPort = process.env.VITE_DEXTER_PORT || process.env.DEXTER_PORT || '8098';
const backend = `http://127.0.0.1:${backendPort}`;
const uiPort = Number(process.env.VITE_DEXTER_UI_PORT || 3006);

export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    port: uiPort,
    strictPort: true,
    host: true,
    // So the dev page can use window.location.origin like the packaged one does.
    proxy: {
      '/api': { target: backend, changeOrigin: false },
      '/health': { target: backend, changeOrigin: false }
    }
  },
  // Frontend smoke tests (roadmap v3 item 7.4 / v4 item R2). The deps were
  // installed but no config or test ever existed, so nothing ran.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  }
});
