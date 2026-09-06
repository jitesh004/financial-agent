import React from 'react';
import { createRoot } from 'react-dom/client';

/* Inter, self-hosted.
 *
 * Bundled from the package rather than fetched from a font CDN, because this
 * app's whole claim is that your statements reach nothing else - and a
 * stylesheet from fonts.googleapis.com is a request to a third party made on
 * every page load, from the same browser, carrying the same IP. It also means
 * the app renders identically on a machine with no internet at all.
 *
 * `wght` only: the weight axis, upright, no optical-size axis and no italic.
 * The subsets are separated by unicode-range, so a browser fetches the ~48KB
 * latin file and nothing else. */
import '@fontsource-variable/inter/wght.css';

import './styles/tokens.css';
import './styles/base.css';
import './styles/app.css';

import { AuthProvider } from './core/auth';
import { RouterProvider } from './core/router';
import { ToastProvider } from './core/toast';
import Gate from './app/Gate';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* Auth sits outside everything so the sign-in screen, the setup wizard
        and the app itself all read one answer to "who is this?" - fetched
        once, on load. */}
    <AuthProvider>
      <RouterProvider>
        <ToastProvider>
          <Gate />
        </ToastProvider>
      </RouterProvider>
    </AuthProvider>
  </React.StrictMode>,
);
