/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    port: 3006,
    strictPort: true,
    host: true
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
