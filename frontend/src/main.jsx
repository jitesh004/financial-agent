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
import App from './App';
import Shell from './Shell';
import { AuthProvider } from './auth';
import './styles.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* The provider sits outside the shell so the sign-in screen, the
        onboarding wizard and the app itself all read one answer to "who is
        this?" - fetched once, on load. */}
    <AuthProvider>
      <Shell>{(props) => <App {...props} />}</Shell>
    </AuthProvider>
  </React.StrictMode>,
);
