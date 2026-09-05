import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '0.0.0.0', // Allows connections when running in Docker
    // Poll for changes instead of waiting to be told about them.
    //
    // The source lives on the host and reaches this container through a bind
    // mount, and a write on the host side of one of those does not raise an
    // inotify event inside the container on Docker Desktop. So Vite's watcher
    // never fired: the file on disk was new, the container could read the new
    // bytes, and the dev server kept serving the version it had transformed
    // at boot. Editing a component appeared to do nothing at all - not after
    // a reload, not after a hard reload - and the only thing that ever picked
    // a change up was restarting the container.
    //
    // A second is slow enough not to spin a laptop's fan over a few hundred
    // files and fast enough that saving still feels immediate.
    watch: {
      usePolling: true,
      interval: 1000,
    },
    // The API stays on its own origin; proxying keeps the browser same-origin
    // so no financial data is ever subject to a CORS preflight round trip.
    proxy: {
      '/api': { 
        target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8078', 
        changeOrigin: true 
      },
    },
  },
})
