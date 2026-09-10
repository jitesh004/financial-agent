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
