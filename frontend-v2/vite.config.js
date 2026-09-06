import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/* V2 build.
 *
 * Two things here are deliberate rather than default.
 *
 * `manualChunks` splits the bundle by what a screen actually needs. The app
 * has twenty-odd screens and nobody opens twenty of them; every route is a
 * dynamic import (see src/app/routes.js), so the first paint downloads the
 * shell, the design system and one screen rather than the whole product.
 *
 * There is no charting library. Every chart in this app is hand-written SVG
 * (src/ui/charts.jsx), which is why the vendor chunk is React and nothing
 * else - about 45KB gzipped for the entire runtime.
 */
export default defineConfig({
  plugins: [react()],
  build: {
    target: 'es2022',
    cssTarget: 'chrome111',
    sourcemap: false,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (id.includes('node_modules/react')) return 'react';
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    // The source lives on the host and reaches this container through a bind
    // mount; a host-side write raises no inotify event inside the container on
    // Docker Desktop, so the watcher never fires and edits appear to do
    // nothing. Polling is the only thing that reliably sees them.
    watch: { usePolling: true, interval: 1000 },
    // Same-origin API, so a session cookie travels and no financial data is
    // ever subject to a CORS preflight.
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8078',
        changeOrigin: true,
      },
    },
  },
});
