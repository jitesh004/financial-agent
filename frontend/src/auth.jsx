import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from './lib';
import { setStorageUser } from './userStorage';

/* Who is signed in, for the whole app.
 *
 * One fetch on load answers three questions at once - is there a session, is
 * Google configured at all, and has this person finished setting up - because
 * the shell has to choose between three entirely different screens before it
 * renders anything, and doing that in three round trips means two visible
 * flashes of the wrong one.
 */

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  /* An error Google handed back through the callback redirect. Read from the
     URL once and then removed, so a refresh does not resurrect it. */
  const [authError, setAuthError] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('auth_error');
  });

  const refresh = useCallback(async () => {
    try {
      const [session, cfg] = await Promise.all([api.session(), api.authConfig()]);
      /* Before setUser, so anything that reads browser-local state while
         rendering the signed-in shell is already looking in this account's
         namespace rather than the previous occupant's. */
      setStorageUser(session.user?.id);
      setUser(session.user);
      setConfig(cfg);
    } catch {
      /* The API being unreachable is not the same as being signed out, but
         from here they look identical and both mean "show the door". */
      setStorageUser(null);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  /* Strip the callback's query parameters once they have been read. They are
     noise in the address bar, and ?gmail=connected re-firing on every reload
     would keep announcing a connection made minutes ago. */
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has('auth_error') || params.has('gmail')) {
      params.delete('auth_error');
      params.delete('gmail');
      const query = params.toString();
      window.history.replaceState(
        {}, '', window.location.pathname + (query ? `?${query}` : ''));
    }
  }, []);

  const signIn = useCallback(() => {
    /* A full-page navigation, not fetch: the consent screen is Google's own
       page and has to own the tab. Where to come back to travels with it. */
    const target = encodeURIComponent(window.location.pathname);
    window.location.href = `/api/auth/google/start?redirect_to=${target}`;
  }, []);

  const signOut = useCallback(async () => {
    try { await api.logout(); } finally { setUser(null); }
  }, []);

  const value = {
    user,
    config,
    loading,
    authError,
    dismissAuthError: () => setAuthError(null),
    signIn,
    signOut,
    refresh,
    /* Applied after finishing the wizard so the shell swaps immediately,
       rather than after another round trip. */
    setUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>');
  return value;
}
