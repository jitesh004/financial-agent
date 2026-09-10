/* ────────────────────────────────────────────────────────────────────────────
   Account menu in the rail footer.

   Signing out used to live only as a low-emphasis link at the bottom of the
   Settings screen, which is not where anyone looks for it, and signing out of
   other devices had no UI at all. Both belong behind the avatar.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from '../core/router';
import { useAuth } from '../core/auth';
import { useToast } from '../core/toast';
import { Icon } from '../ui/icons';
import { Spinner } from '../ui';

export default function AccountMenu({ collapsed, onNavigate }) {
  const { user, isAdmin, signOut, signOutEverywhere } = useAuth();
  const toast = useToast();
  const anchorRef = useRef(null);
  const menuRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(null);
  const [busy, setBusy] = useState(null);
  const [confirmAll, setConfirmAll] = useState(false);

  /* Anchored through a portal rather than positioned inside the rail: the rail
     is `overflow: hidden`, so a popover rendered in place is clipped away. */
  const place = () => {
    const r = anchorRef.current?.getBoundingClientRect();
    if (!r) return;
    setAt({ left: Math.max(8, r.left), bottom: window.innerHeight - r.top + 8 });
  };

  useEffect(() => {
    if (!open) return undefined;
    place();
    const onDown = (e) => {
      if (menuRef.current?.contains(e.target) || anchorRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', place);
    };
  }, [open]);

  useEffect(() => { if (!open) setConfirmAll(false); }, [open]);

  if (!user) return null;

  const initials = (user.display_name || user.name || user.email || '?')
    .split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase();

  const doSignOut = async () => {
    setBusy('one');
    try {
      await signOut();
    } catch (e) {
      toast.fail('Could not sign out', e.message);
      setBusy(null);
    }
  };

  const doSignOutAll = async () => {
    setBusy('all');
    try {
      const result = await signOutEverywhere();
      const ended = result?.sessions_ended;
      toast.ok('Signed out everywhere',
        ended ? `${ended} session${ended === 1 ? '' : 's'} ended.` : undefined);
    } catch (e) {
      toast.fail('Could not sign out everywhere', e.message);
      setBusy(null);
    }
  };

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        className="account-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        title={`${user.display_name || user.name || user.email} — account and sign out`}
        onClick={() => setOpen((v) => !v)}
      >
        {user.picture
          ? <img src={user.picture} alt="" className="account-avatar" width={26} height={26} />
          : <span className="account-avatar account-avatar-fallback">{initials}</span>}
        {!collapsed && (
          <span className="account-trigger-body">
            <span className="account-name truncate">{user.display_name || user.name}</span>
            <span className="account-email truncate">{user.email}</span>
          </span>
        )}
        {!collapsed && <Icon name="chevron-up" size={14} style={{ flexShrink: 0, opacity: 0.6 }} />}
      </button>

      {open && at && createPortal(
        <div
          ref={menuRef}
          className="account-menu"
          role="menu"
          style={{ left: at.left, bottom: at.bottom }}
        >
          <div className="account-menu-head">
            <div className="account-name">{user.display_name || user.name}</div>
            <div className="account-email">{user.email}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 7 }}>
              {isAdmin && <span className="chip chip-accent tiny">Admin</span>}
              {user.demo_mode && <span className="chip chip-warn tiny">Demo mode</span>}
            </div>
          </div>

          <Link
            to="/profile"
            role="menuitem"
            className="account-menu-item"
            onClick={() => { setOpen(false); onNavigate?.(); }}
          >
            <Icon name="user" size={15} />
            <span>Security &amp; PDF passwords</span>
          </Link>
          <Link
            to="/settings"
            role="menuitem"
            className="account-menu-item"
            onClick={() => { setOpen(false); onNavigate?.(); }}
          >
            <Icon name="settings" size={15} />
            <span>Settings &amp; active devices</span>
          </Link>

          <div className="account-menu-rule" />

          <button
            type="button"
            role="menuitem"
            className="account-menu-item"
            disabled={Boolean(busy)}
            onClick={doSignOut}
          >
            {busy === 'one' ? <Spinner sm /> : <Icon name="logout" size={15} />}
            <span>Sign out</span>
          </button>

          {confirmAll ? (
            <div className="account-menu-confirm">
              <div className="tiny muted" style={{ marginBottom: 8 }}>
                Ends every session on every device, including this one.
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={Boolean(busy)}
                  onClick={doSignOutAll}
                >
                  {busy === 'all' ? <Spinner sm /> : null}
                  Sign out everywhere
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setConfirmAll(false)}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              role="menuitem"
              className="account-menu-item account-menu-item-danger"
              disabled={Boolean(busy)}
              onClick={() => setConfirmAll(true)}
            >
              <Icon name="shield" size={15} />
              <span>Sign out of all devices</span>
            </button>
          )}
        </div>,
        document.body,
      )}
    </>
  );
}
