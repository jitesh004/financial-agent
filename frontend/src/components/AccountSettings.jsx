import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../auth';
import { api, dateLabel } from '../lib';
import { Callout, Card, Chip, ConfirmButton } from './ui';

/* Everything about the account rather than about the ledger: what this app is
 * allowed to reach, which devices are signed in, and how to leave.
 *
 * All three are here because they answer the same question - "what does this
 * thing have of mine, and can I take it back?" - and a privacy promise you
 * cannot act on from inside the product is not much of a promise.
 */
export default function AccountSettings() {
  const { user, refresh } = useAuth();
  const [gmail, setGmail] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const [confirmEmail, setConfirmEmail] = useState('');

  const load = useCallback(() => {
    api.gmailStatus().then(setGmail).catch(() => setGmail(null));
    api.activeSessions()
      .then((body) => setSessions(body.sessions || []))
      .catch(() => setSessions([]));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function disconnectGmail() {
    setError(null);
    try {
      await api.gmailDisconnect();
      setNote('Gmail disconnected. The grant has been revoked at Google too.');
      load();
    } catch (e) { setError(e.message); }
  }

  async function endOtherSessions() {
    setError(null);
    try { await api.logoutEverywhere(); } finally { await refresh(); }
  }

  async function deleteAccount() {
    setError(null);
    try {
      await api.deleteAccount(confirmEmail);
      await refresh();
    } catch (e) { setError(e.message); }
  }

  if (!user) return null;

  return (
    <>
      {error && <Callout tone="neg">{error}</Callout>}
      {note && <Callout tone="pos">{note}</Callout>}

      <Card title="Your mailbox"
        sub={gmail?.connected ? 'Connected' : 'Not connected'}>
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 0 }}>
          Read-only access to Gmail, so statements are found rather than
          downloaded by hand. Separate from signing in, and safe to withdraw:
          everything already imported stays exactly where it is.
        </p>
        {gmail?.connected ? (
          <ConfirmButton
            question="Disconnect Gmail? Imported statements are kept."
            confirmLabel="Disconnect"
            onConfirm={disconnectGmail}
          >
            Disconnect Gmail
          </ConfirmButton>
        ) : gmail?.available ? (
          <button className="btn primary"
            onClick={() => api.gmailConnect()}>Connect Gmail</button>
        ) : (
          <Callout tone="warn">
            {gmail?.setup_hint || 'Mailbox import is not configured on this server.'}
          </Callout>
        )}
      </Card>

      <Card title="Signed in on" sub={`${sessions.length} device(s)`}>
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 0 }}>
          Every browser holding a live session. Sessions are stored on the
          server, which is what makes ending one from here actually end it.
        </p>
        <table className="table">
          <thead>
            <tr><th>Device</th><th>Last used</th><th>Signed in</th><th /></tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={`${s.issued_at}-${s.user_agent}`}>
                <td>
                  <div className="truncate" style={{ maxWidth: 320 }}
                    title={s.user_agent}>
                    {describe(s.user_agent)}
                  </div>
                </td>
                <td>{dateLabel(s.last_used_at)}</td>
                <td>{dateLabel(s.issued_at)}</td>
                <td>{s.current && <Chip tone="accent">This one</Chip>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ marginTop: 12 }}>
          <ConfirmButton
            question="Sign out everywhere, including here?"
            confirmLabel="Sign out everywhere"
            onConfirm={endOtherSessions}
          >
            Sign out on every device
          </ConfirmButton>
        </div>
      </Card>

      <Card title="Delete your account" sub="Irreversible">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 0 }}>
          Removes every statement, transaction, correction, claim, dashboard
          and uploaded file belonging to <strong>{user.email}</strong>. There is
          no undo and no copy kept.
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center',
          flexWrap: 'wrap' }}>
          <input
            value={confirmEmail}
            onChange={(e) => setConfirmEmail(e.target.value)}
            placeholder="Type your email address to confirm"
            style={{ flex: '1 1 260px' }}
          />
          <ConfirmButton
            className="btn danger"
            question="Delete everything, permanently?"
            confirmLabel="Delete my account"
            disabled={confirmEmail.trim().toLowerCase() !== user.email.toLowerCase()}
            onConfirm={deleteAccount}
          >
            Delete my account
          </ConfirmButton>
        </div>
      </Card>
    </>
  );
}

/* A user-agent string is unreadable; the browser and platform out of it are
   enough to recognise your own laptop in a list of three. */
function describe(agent) {
  if (!agent) return 'Unknown device';
  const browser = /Edg\//.test(agent) ? 'Edge'
    : /OPR\//.test(agent) ? 'Opera'
      : /Firefox\//.test(agent) ? 'Firefox'
        : /Chrome\//.test(agent) ? 'Chrome'
          : /Safari\//.test(agent) ? 'Safari' : 'Browser';
  const platform = /Windows/.test(agent) ? 'Windows'
    : /Mac OS X/.test(agent) ? 'macOS'
      : /Android/.test(agent) ? 'Android'
        : /(iPhone|iPad)/.test(agent) ? 'iOS'
          : /Linux/.test(agent) ? 'Linux' : '';
  return platform ? `${browser} on ${platform}` : browser;
}
