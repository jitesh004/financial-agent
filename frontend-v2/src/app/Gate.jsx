/* Which of the three screens the app is on.
 *
 * There are exactly three states and they are mutually exclusive: nobody
 * signed in, signed in but not set up, and the app proper. Deciding between
 * them in one place means no screen has to defend itself against being
 * rendered without a user.
 */

import React, { lazy, Suspense, useState } from 'react';
import { useAuth } from '../core/auth';
import App from './App';

const SignIn = lazy(() => import('../screens/SignIn'));
const Onboarding = lazy(() => import('../screens/Onboarding'));

export default function Gate() {
  const { user, loading, setUser } = useAuth();
  /* Set when the wizard finishes with "Import statements", so the app opens
     the import flow on its first render rather than dropping somebody who just
     asked to fill a dashboard on an empty one. */
  const [openImport, setOpenImport] = useState(false);

  if (loading) {
    return (
      <div className="boot">
        <div className="boot-mark" />
      </div>
    );
  }

  if (!user) {
    return <Suspense fallback={<Booting />}><SignIn /></Suspense>;
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
  return <div className="boot"><div className="boot-mark" /></div>;
}
