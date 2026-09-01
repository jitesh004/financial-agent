import React, { useEffect, useState } from 'react';
import Onboarding from './components/Onboarding';
import SignIn from './components/SignIn';
import { useAuth } from './auth';
import { setUnauthorizedHandler } from './lib';

/* Which of the three screens the app is on.
 *
 * Kept out of App.jsx so that file stays about the dashboard. There are
 * exactly three states and they are mutually exclusive: nobody signed in,
 * signed in but not set up, and the app proper. Deciding between them in one
 * place means no panel has to defend itself against being rendered without a
 * user.
 */
export default function Shell({ children }) {
  const { user, loading, refresh, setUser } = useAuth();
  /* Set when the wizard finishes with "Import statements", so the app opens
     the import flow on its first render rather than dropping the person on an
     empty dashboard they just asked to fill. */
  const [openImport, setOpenImport] = useState(false);

  /* A 401 from anywhere means the session ended - it expired, or was revoked
     from another device. Re-reading it swaps the shell to the sign-in screen
     rather than leaving a dashboard on screen that can no longer load. */
  useEffect(() => {
    setUnauthorizedHandler(() => refresh());
    return () => setUnauthorizedHandler(null);
  }, [refresh]);

  if (loading) {
    return (
      <div className="boot">
        <div className="spinner" />
      </div>
    );
  }

  if (!user) return <SignIn />;

  if (!user.onboarded) {
    return (
      <Onboarding
        onFinished={(updated) => { setUser(updated); setOpenImport(false); }}
        onImport={() => setOpenImport(true)}
      />
    );
  }

  return children({ openImport, onImportOpened: () => setOpenImport(false) });
}
