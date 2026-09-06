/* Who is signed in, for the whole app.
 *
 * One fetch on load answers three questions at once - is there a session, is
 * Google configured at all, and has this person finished setting up - because
 * the shell has to choose between three entirely different screens before it
 * renders anything, and doing that in three round trips means two visible
 * flashes of the wrong one.
 */

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import { api, setUnauthorizedHandler } from './api';
import { clearCache } from './store';
import { setStorageUser } from './storage';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [config, setConfig] = useState(null);
  /* Whether this person runs the deployment. Decided on the server from
     FA_ADMIN_EMAILS; this only decides whether a screen is offered, and every
     admin endpoint checks for itself. */
  const [isAdmin, setIsAdmin] = useState(false);
  const [loading, setLoading] = useState(true);
  /* An error Google handed back through the callback redirect. Read from the
     URL once and then removed, so a refresh does not resurrect it. */
  const [authError, setAuthError] = useState(
    () => new URLSearchParams(window.location.search).get('auth_error'),
  );

  const refresh = useCallback(async () => {
    try {
      const [session, cfg] = await Promise.all([api.session(), api.authConfig()]);
      /* Before setUser, so anything reading browser-local state while
         rendering the signed-in shell is already looking in this account's
         namespace rather than the previous occupant's. */
      setStorageUser(session.user?.id);
      setUser(session.user);
      setIsAdmin(Boolean(session.is_admin));
      setConfig(cfg);
    } catch {
      /* The API being unreachable is not the same as being signed out, but
         from here they look identical and both mean "show the door". */
      setStorageUser(null);
      setUser(null);
      setIsAdmin(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  /* A 401 from anywhere means the session ended - expired, or revoked from
     another device. Re-reading it swaps the shell to the sign-in screen rather
     than leaving a dashboard on screen that can no longer load. */
  useEffect(() => {
    setUnauthorizedHandler(() => { clearCache(); refresh(); });
    return () => setUnauthorizedHandler(null);
  }, [refresh]);

  /* Strip the callback's query parameters once read. They are noise in the
     address bar, and ?gmail=connected re-firing on every reload would keep
     announcing a connection made minutes ago. */
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.has('auth_error') && !params.has('gmail')) return;
    params.delete('auth_error');
    params.delete('gmail');
    const q = params.toString();
    window.history.replaceState({}, '', window.location.pathname + (q ? `?${q}` : ''));
  }, []);

  const signIn = useCallback(() => {
    /* A full-page navigation, not fetch: the consent screen is Google's own
       page and has to own the tab. Where to come back to travels with it. */
    window.location.href = api.signInUrl(window.location.pathname);
  }, []);

  const signOut = useCallback(async () => {
    try { await api.logout(); } finally { clearCache(); setUser(null); }
  }, []);

  const value = useMemo(() => ({
    user,
    config,
    isAdmin,
    loading,
    authError,
    dismissAuthError: () => setAuthError(null),
    signIn,
    signOut,
    refresh,
    /* Applied after finishing the wizard so the shell swaps immediately rather
       than after another round trip. */
    setUser,
  }), [user, config, isAdmin, loading, authError, signIn, signOut, refresh]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>');
  return value;
}
