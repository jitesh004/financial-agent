import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/* Prism v3 build.
 *
 * Next-generation financial intelligence frontend.
 * - Dynamic route code-splitting via manualChunks
 * - Direct zero-overhead SVG charting engine
 * - Same-origin proxy to FastAPI backend
 */
export default defineConfig({
  plugins: [react()],
  build: {
    target: 'es2022',
    cssTarget: 'chrome111',
    sourcemap: false,
    chunkSizeWarningLimit: 800,
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
    port: 5175,
    host: '0.0.0.0',
    watch: { usePolling: true, interval: 1000 },
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8078',
        changeOrigin: true,
      },
    },
  },
});
