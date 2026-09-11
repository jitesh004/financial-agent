/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Bootstrap Entry Point
   ──────────────────────────────────────────────────────────────────────── */

import React from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/inter/wght.css';

import './styles/tokens.css';
import './styles/base.css';
import './styles/animations.css';
import './styles/app.css';

import { AuthProvider } from './core/auth';
import { RouterProvider } from './core/router';
import { ToastProvider } from './core/toast';
import Gate from './app/Gate';

/* Take the boot screen down once the app has actually painted.

   After a frame where possible, because `render()` returns before the
   browser has drawn anything and removing it immediately swaps a splash
   for a blank screen for one frame - the flicker it exists to prevent.

   But NOT depending on that frame ever arriving. `requestAnimationFrame`
   does not fire in a tab that is hidden, backgrounded or minimised, and a
   dismissal built only on rAF simply never runs there: the splash stays
   in the DOM for the life of the page. Measured, not guessed - a probe in
   this very file recorded exactly one frame in 2.5 seconds with the
   window hidden.

   So the timeout is the guarantee and the frame is the optimisation,
   rather than the other way round. */
function dismissBootScreen() {
  const boot = document.getElementById('boot');
  if (!boot) return;

  let done = false;
  const remove = () => {
    if (done) return;
    done = true;
    boot.classList.add('done');
    boot.addEventListener('transitionend', () => boot.remove(), { once: true });
    // `transitionend` does not fire for an element that never transitions
    // - reduced motion, a hidden tab, a display change - so the node is
    // swept up unconditionally a moment later.
    setTimeout(() => boot.remove(), 400);
  };

  // Scheduled independently: whichever arrives first wins, and in a hidden
  // tab only the timer ever does.
  requestAnimationFrame(() => requestAnimationFrame(remove));
  setTimeout(remove, 150);
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthProvider>
      <RouterProvider>
        <ToastProvider>
          <Gate />
        </ToastProvider>
      </RouterProvider>
    </AuthProvider>
  </React.StrictMode>,
);

dismissBootScreen();
