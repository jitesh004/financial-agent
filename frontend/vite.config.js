import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API stays on its own origin; proxying keeps the browser same-origin
    // so no financial data is ever subject to a CORS preflight round trip.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8078', changeOrigin: true },
    },
  },
})
