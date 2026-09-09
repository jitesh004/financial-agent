import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/* The v2 test runner.
 *
 * Separate from vite.config.js so the dev server's proxy and manual chunking
 * play no part in a test run, and so `test.environment` cannot leak into a
 * production build.
 *
 * Every screen here is exercised against a fake API (see test/harness.jsx),
 * not a mocked module graph: the screens ask `core/api` for data exactly as
 * they do in the browser, and the harness answers with the shapes the real
 * FastAPI server returns. The shapes themselves are checked against the
 * running server by backend/tests/test_v2_contract.py, so a fixture that
 * drifts from the API is a failing test rather than a green one.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./test/setup.js'],
    include: ['test/**/*.test.{js,jsx}'],
    // Screens fetch on mount and settle over a few microtasks; the default is
    // ample, but a slow CI box should not turn that into a flake.
    testTimeout: 15000,
    restoreMocks: true,
  },
});
