/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Gate: Authentication & Onboarding state switch
   ──────────────────────────────────────────────────────────────────────── */

import React, { lazy, Suspense, useState } from 'react';
import { useAuth } from '../core/auth';
import App from './App';

const SignIn = lazy(() => import('../screens/SignIn'));
const Onboarding = lazy(() => import('../screens/Onboarding'));

export default function Gate() {
  const { user, loading, setUser } = useAuth();
  const [openImport, setOpenImport] = useState(false);

  if (loading) {
    return <Booting />;
  }

  if (!user) {
    return (
      <Suspense fallback={<Booting />}>
        <SignIn />
      </Suspense>
    );
  }

  if (!user.onboarded) {
    return (
      <Suspense fallback={<Booting />}>
        <Onboarding
          onFinished={(updated) => { setUser(updated); setOpenImport(false); }}
          onImport={() => setOpenImport(true)}
        />
      </Suspense>
    );
  }

  return <App openImport={openImport} onImportOpened={() => setOpenImport(false)} />;
}

function Booting() {
  return (
    <div style={{
      height: '100vh', width: '100vw', display: 'flex',
      alignItems: 'center', justifyContent: 'center', background: 'var(--bg)',
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: '50%',
        border: '3px solid var(--surface-3)', borderTopColor: 'var(--accent)',
        animation: 'spin 0.8s linear infinite',
      }} />
    </div>
  );
}
