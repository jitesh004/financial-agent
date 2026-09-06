/* The top bar: where you are, what is waiting, and who you are.
 *
 * Everything here is either about the current screen or about the app as a
 * whole. Nothing screen-specific goes in it - a bar that changes shape between
 * screens is a bar people stop reading.
 */

import React, { useRef, useState } from 'react';
import { useAuth } from '../core/auth';
import { useTheme } from '../core/theme';
import { useRoute } from '../core/router';
import { useWorkflow } from '../core/ledger';
import { api } from '../core/api';
import { count } from '../core/format';
import {
  Button, Chip, Icon, IconButton, Menu, MenuItem, Popover,
} from '../ui';

/* ── setup status ────────────────────────────────────────────────────────── */

/* Where the workspace stands: what is done, and what is waiting on you.
 *
 * Derived fresh from stored data on every request rather than tracked as a
 * "current step" pointer. A stored pointer is a second source of truth about
 * state the database already knows, and the two drift the moment anything
 * happens out of band - a file retried from the coverage grid, a restart
 * mid-import, a decision recorded from the ledger.
 *
 * It lives behind one button rather than as a strip above every screen: it
 * answers a question you ask on the days you are importing statements, not on
 * the days you are reading them.
 */
const WHERE = {
  sources: '/data', collect: '/data', parse: '/data',
  review: '/review', analyze: '/', profile: '/profile',
};

function SetupButton({ onImport }) {
  const { data, refetch } = useWorkflow();
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  const { navigate } = useRoute();

  if (!data) return null;
  const stages = data.stages || [];
  const outstanding = stages.filter((s) => !s.complete);
  const counts = data.counts || {};

  return (
    <>
      <button
        ref={anchor}
        className={`btn ${outstanding.length ? '' : 'ghost'}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={outstanding.length
          ? `${outstanding.length} setup step${outstanding.length === 1 ? '' : 's'} waiting: `
            + outstanding.map((s) => s.label).join(', ')
          : 'Setup is complete — nothing waiting on you'}
        onClick={() => { setOpen((v) => !v); refetch(); }}
      >
        <Icon name={outstanding.length ? 'target' : 'check-circle'} size={14} />
        {outstanding.length > 0 && <span className="rail-count">{outstanding.length}</span>}
      </button>

      {open && (
        <Popover anchorRef={anchor} onClose={() => setOpen(false)} width={340}>
          <div className="pop-head row">
            <strong>Setup</strong>
            {outstanding.length
              ? <Chip tone="warn">{outstanding.length} to do</Chip>
              : <Chip tone="pos">all done</Chip>}
          </div>
          <p className="small muted" style={{ padding: '0 9px 8px' }}>
            {outstanding.length
              ? 'Each of these is optional — the app works without any of them. They are '
                + 'listed because each one makes the figures more complete.'
              : 'Nothing is waiting on you. Every statement known to this workspace is '
                + 'parsed, reviewed and counted.'}
          </p>
          <div className="pop-sep" />
          {stages.map((stage, i) => (
            <button
              key={stage.id}
              type="button"
              className="pop-item"
              title={stage.complete ? stage.detail : `Go and fix: ${stage.label}`}
              onClick={() => {
                setOpen(false);
                const target = WHERE[stage.id];
                if (target) navigate(target);
              }}
            >
              <span className={`step-dot ${stage.complete ? 'done' : ''}`}
                style={stage.complete
                  ? { background: 'var(--pos)', color: '#fff' } : undefined}>
                {stage.complete ? '✓' : i + 1}
              </span>
              <span style={{ minWidth: 0 }}>
                <span style={{ display: 'block', fontWeight: 560 }}>{stage.label}</span>
                <span className="tiny dim" style={{ display: 'block', lineHeight: 1.45 }}>
                  {stage.detail}
                </span>
              </span>
            </button>
          ))}
          <div className="pop-sep" />
          <div className="row" style={{ padding: '2px 9px 6px' }}>
            <Button variant="primary" size="sm" icon="upload"
              onClick={() => { setOpen(false); onImport(); }}>
              Import statements
            </Button>
            <span className="tiny dim">
              {counts.transactions ? `${count(counts.transactions)} rows` : 'nothing imported yet'}
              {counts.files ? ` · ${counts.files} file${counts.files === 1 ? '' : 's'}` : ''}
              {counts.missing_months
                ? ` · ${counts.missing_months} month${counts.missing_months === 1 ? '' : 's'} missing`
                : ''}
            </span>
          </div>
        </Popover>
      )}
    </>
  );
}

/* ── account menu ────────────────────────────────────────────────────────── */

/* "Sign out everywhere" is here rather than buried in Settings because it is
   the one control somebody reaches for in a hurry - a laptop left at an
   office, a shared machine - and a control you need in a hurry should not take
   three clicks to find. */
function AccountMenu() {
  const { user, signOut, refresh } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const { navigate } = useRoute();
  const [busy, setBusy] = useState(false);
  if (!user) return null;

  const initials = (user.display_name || user.email)
    .split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase()).join('');

  return (
    <Menu
      width={250}
      trigger={(
        <button className="avatar" aria-label="Your account" title={user.email}>
          {user.picture
            ? <img src={user.picture} alt="" referrerPolicy="no-referrer" />
            : <span>{initials || '?'}</span>}
        </button>
      )}
    >
      {(close) => (
        <>
          <div className="pop-head">
            <div style={{ fontWeight: 600 }}>{user.display_name}</div>
            <div className="tiny dim" style={{ overflowWrap: 'anywhere' }}>{user.email}</div>
          </div>
          <div className="pop-sep" />
          <MenuItem icon="user" onClick={() => { close(); navigate('/profile'); }}>
            Your details
          </MenuItem>
          <MenuItem icon="settings" onClick={() => { close(); navigate('/settings'); }}>
            Settings
          </MenuItem>
          <MenuItem
            icon="refresh"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try { await api.onboardingReopen(); } finally { await refresh(); }
            }}
          >
            Run setup again
          </MenuItem>
          <div className="pop-sep" />
          <MenuItem icon={theme === 'dark' ? 'sun' : 'moon'} onClick={toggleTheme}>
            {theme === 'dark' ? 'Light theme' : 'Dark theme'}
            <span className="spacer" />
            <kbd>⇧D</kbd>
          </MenuItem>
          <div className="pop-sep" />
          <MenuItem icon="logout" onClick={() => { close(); signOut(); }}>Sign out</MenuItem>
          <MenuItem
            icon="lock"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try { await api.logoutEverywhere(); } finally { await refresh(); }
            }}
          >
            Sign out on every device
          </MenuItem>
        </>
      )}
    </Menu>
  );
}

/* ── the bar ─────────────────────────────────────────────────────────────── */

export default function Topbar({ title, onMenu, onCommand, onImport, importBadge }) {
  return (
    <header className="topbar">
      <IconButton icon="menu" label="Sections" className="ghost only-mobile"
        onClick={onMenu} />
      <div className="topbar-title">{title}</div>

      <span className="topbar-spacer" />

      <button className="cmd-trigger" onClick={onCommand} aria-label="Search and commands">
        <Icon name="search" size={14} />
        <span>Search or jump to…</span>
        <kbd>⌘K</kbd>
      </button>

      <SetupButton onImport={onImport} />

      <Button icon="upload" onClick={onImport} title="Scan your mailbox, or add files">
        Import
        {importBadge > 0 && <span className="rail-count">{importBadge}</span>}
      </Button>

      <AccountMenu />
    </header>
  );
}

