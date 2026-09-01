import React, { useEffect, useRef, useState } from 'react';
import { useAuth } from '../auth';
import { api } from '../lib';

/* Who is signed in, and the handful of actions that belong to the person
 * rather than to the ledger: their details, running setup again, signing out
 * of this device or of all of them.
 *
 * "Sign out everywhere" is here rather than buried in Settings because it is
 * the one control someone reaches for in a hurry - a laptop left at an office,
 * a shared machine they forgot to sign out of - and a control you need in a
 * hurry should not take three clicks to find.
 */
export default function AccountMenu({ onProfile }) {
  const { user, signOut, refresh } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => { if (!box.current?.contains(e.target)) setOpen(false); };
    const escape = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  if (!user) return null;

  const initials = (user.display_name || user.email)
    .split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase()).join('');

  async function everywhere() {
    setBusy(true);
    try { await api.logoutEverywhere(); } finally { await refresh(); }
  }

  async function runSetupAgain() {
    setBusy(true);
    try { await api.onboardingReopen(); } finally { await refresh(); }
  }

  return (
    <div className="account" ref={box}>
      <button className="account-button" aria-haspopup="menu"
        aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {user.picture
          ? <img src={user.picture} alt="" referrerPolicy="no-referrer" />
          : <span className="account-initials">{initials || '?'}</span>}
      </button>

      {open && (
        <div className="account-menu" role="menu">
          <div className="account-who">
            <div className="account-name">{user.display_name}</div>
            <div className="account-email">{user.email}</div>
          </div>

          <button role="menuitem" onClick={() => { setOpen(false); onProfile(); }}>
            Your details
          </button>
          <button role="menuitem" onClick={runSetupAgain} disabled={busy}>
            Run setup again
          </button>

          <div className="account-rule" />

          <button role="menuitem" onClick={signOut} disabled={busy}>
            Sign out
          </button>
          <button role="menuitem" onClick={everywhere} disabled={busy}>
            Sign out on every device
          </button>
        </div>
      )}
    </div>
  );
}
