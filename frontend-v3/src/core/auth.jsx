/* ────────────────────────────────────────────────────────────────────────────
   Authentication & Session Provider
   ──────────────────────────────────────────────────────────────────────── */

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
  const [isAdmin, setIsAdmin] = useState(false);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState(
    () => new URLSearchParams(window.location.search).get('auth_error'),
  );

  const refresh = useCallback(async () => {
    try {
      const [session, cfg] = await Promise.all([api.session(), api.authConfig()]);
      setStorageUser(session.user?.id);
      setUser(session.user);
      setIsAdmin(Boolean(session.is_admin));
      setConfig(cfg);
    } catch {
      setStorageUser(null);
      setUser(null);
      setIsAdmin(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    setUnauthorizedHandler(() => { clearCache(); refresh(); });
    return () => setUnauthorizedHandler(null);
  }, [refresh]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.has('auth_error') && !params.has('gmail')) return;
    params.delete('auth_error');
    params.delete('gmail');
    const q = params.toString();
    window.history.replaceState({}, '', window.location.pathname + (q ? `?${q}` : ''));
  }, []);

  const signIn = useCallback(() => {
    window.location.href = api.signInUrl(window.location.pathname);
  }, []);

  const signOut = useCallback(async () => {
    try { await api.logout(); } finally { clearCache(); setUser(null); }
  }, []);

  /* Ends every session for this account, including the one making the call -
     the server clears this device's cookie in the same response. Local state
     is cleared regardless of the outcome: a failed sign-out that leaves the
     app looking signed in is worse than one that asks you to sign in again. */
  const signOutEverywhere = useCallback(async () => {
    try {
      return await api.logoutEverywhere();
    } finally {
      clearCache();
      setUser(null);
    }
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
    signOutEverywhere,
    refresh,
    setUser,
  }), [user, config, isAdmin, loading, authError, signIn, signOut,
      signOutEverywhere, refresh]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
