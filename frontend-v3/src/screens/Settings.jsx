import React, { useState } from 'react';
import { api, switchDemo } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { useAuth } from '../core/auth';
import { useToast } from '../core/toast';
import { usePrefs, PREFS } from '../core/prefs';
import { ago, count, dateLabel, monthLabelLong, stampLabel, titleCase } from '../core/format';
import { Button, Callout, Card, Chip, ConfirmButton, Loading, Select, Switch, Badge } from '../ui';
import JobProgress from '../ui/JobProgress';
import LlmSettings from './settings/LlmSettings';

export default function Settings() {
  return (
    <div className="settings-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Preferences & Environment</h1>
            <Badge tone="brand" size="sm">System Configuration</Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Configure language model classification, connected authentication credentials, custom category taxonomies, and display density.
          </p>
        </div>
      </div>

      <ModelSettings />
      <LlmSettings />
      <AccountSettings />
      <DemoSettings />
      <CategoriesManager />
      <DisplaySettings />
    </div>
  );
}

function ModelSettings() {
  const toast = useToast();
  const { data: settings, refetch } = useQuery('settings', () => api.settings());
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);

  if (!settings) return null;
  const pending = settings.uncategorized_count || 0;
  const done = job && !job.active && job.status === 'complete';

  const toggle = async (next) => {
    try {
      await api.saveSettings({ use_llm: next });
      refetch();
      toast.ok('Model inference preferences updated');
    } catch (e) {
      toast.fail('Update failed', e.message);
    }
  };

  const run = async () => {
    setBusy(true);
    try {
      const { job_id: id } = await api.runCategorize();
      for (;;) {
        const current = await api.job(id).catch(() => null);
        if (!current) break;
        setJob(current);
        if (!current.active) break;
        await new Promise((r) => { setTimeout(r, 800); });
      }
      invalidate('dashboard', 'analysis', 'txns', 'workflow', 'settings');
      refetch();
    } catch (e) {
      toast.fail('Inference run failed', e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Semantic AI Classification"
      subtitle={settings.llm_configured
        ? `Answering on ${settings.llm_provider}${settings.llm_model ? ` · ${settings.llm_model}` : ''}`
        : 'No model provider is configured yet'}
    >
      <p className="small muted" style={{ marginBottom: 'var(--space-3)' }}>
        Deterministic regex rules categorize over 95% of rows without calling external APIs.
        For ambiguous merchant narrations, a language model can propose high-confidence categories.
      </p>

      {!settings.llm_configured && (
        <Callout tone="warn" style={{ marginBottom: 'var(--space-3)' }}>
          No model is reachable. Pick a provider and paste an API key in
          <strong> Language Model Provider</strong> below, or set one in the
          server&rsquo;s <code>.env</code>.
        </Callout>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: 'var(--space-3) 0', borderTop: '1px solid var(--border-subtle)' }}>
        <div>
          <div className="font-semibold small">Enable Language Model Fallback for Uncategorized Rows</div>
          <div className="tiny muted">Metered inference only executes when deterministic rules do not fire.</div>
        </div>
        <Switch
          checked={Boolean(settings.use_llm)}
          disabled={!settings.llm_configured}
          onChange={toggle}
          label="Model toggle"
        />
      </div>

      <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center', marginTop: 'var(--space-3)' }}>
        <Badge tone={pending ? 'warn' : 'pos'} size="md">
          {pending} uncategorized row{pending === 1 ? '' : 's'}
        </Badge>
        <Button
          variant="primary"
          size="sm"
          busy={busy}
          disabled={!settings.use_llm || !pending}
          onClick={run}
        >
          Run AI Categorizer on {pending} Rows
        </Button>
      </div>

      {job && <div style={{ marginTop: 'var(--space-3)' }}><JobProgress job={job} title="Classifying" /></div>}

      {done && job.result && (
        <Callout tone="pos" style={{ marginTop: 'var(--space-3)' }}>
          <strong>{job.result.updated} of {job.result.considered} rows classified.</strong>
          {' '}{job.result.changed_from_cache} matched via learned cache and {job.result.changed_from_model} via model inference.
        </Callout>
      )}
    </Card>
  );
}

function AccountSettings() {
  const { user, signOut, signOutEverywhere, isAdmin } = useAuth();
  const toast = useToast();
  const { data, loading, error, refetch } = useQuery('sessions', () => api.activeSessions());
  const [busy, setBusy] = useState(null);

  const sessions = data?.sessions || [];
  const others = sessions.filter((x) => !x.current).length;

  const endThisDevice = async () => {
    setBusy('one');
    try {
      await signOut();
    } catch (e) {
      toast.fail('Could not sign out', e.message);
      setBusy(null);
    }
  };

  const endEverywhere = async () => {
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
    <Card
      title="Account & Active Devices"
      subtitle="Sessions live on the server, so any of them can be ended from here — including ones you no longer have the device for."
    >
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        gap: 'var(--space-4)', flexWrap: 'wrap',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', minWidth: 0 }}>
          {user?.picture && (
            <img
              src={user.picture}
              alt=""
              width={38}
              height={38}
              style={{ borderRadius: '50%', flexShrink: 0 }}
            />
          )}
          <div style={{ minWidth: 0 }}>
            <div className="font-semibold truncate">{user?.display_name || user?.name || 'Signed in'}</div>
            <div className="tiny muted truncate">{user?.email}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 5 }}>
              <Chip size="sm">Google OAuth 2.0</Chip>
              {isAdmin && <Chip tone="brand" size="sm">Admin</Chip>}
              {user?.created_at && <Chip size="sm">Joined {dateLabel(user.created_at)}</Chip>}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
          <Button
            icon="logout"
            busy={busy === 'one'}
            disabled={Boolean(busy)}
            onClick={endThisDevice}
          >
            Sign out
          </Button>
          <ConfirmButton
            variant="danger"
            icon="shield"
            disabled={Boolean(busy)}
            question={others
              ? `End this session and ${others} other${others === 1 ? '' : 's'}?`
              : 'End every session for this account?'}
            confirmLabel="Sign out everywhere"
            onConfirm={endEverywhere}
          >
            Sign out of all devices
          </ConfirmButton>
        </div>
      </div>

      <div style={{
        marginTop: 'var(--space-4)', paddingTop: 'var(--space-3)',
        borderTop: '1px solid var(--border-subtle)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <div className="font-semibold small">
            Active sessions
            {sessions.length > 0 && (
              <span className="tiny muted" style={{ marginLeft: 6 }}>
                {count(sessions.length)} signed in
                {others ? ` · ${count(others)} on other device${others === 1 ? '' : 's'}` : ' · this device only'}
              </span>
            )}
          </div>
          <Button size="sm" variant="ghost" icon="refresh" onClick={refetch}>Refresh</Button>
        </div>

        {loading && <Loading message="Reading active sessions…" />}
        {error && <Callout tone="warn">Could not list sessions: {error.message}</Callout>}

        {!loading && !error && (
          <div className="table-wrapper" style={{ marginTop: 'var(--space-2)' }}>
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>Device</th>
                  <th>IP address</th>
                  <th>Signed in</th>
                  <th>Last used</th>
                  <th>Expires</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((sess, i) => (
                  <tr key={`${sess.issued_at}-${i}`} className="terminal-row">
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span className="font-medium">{describeAgent(sess.user_agent)}</span>
                        {sess.current && <Chip tone="pos" size="sm">This device</Chip>}
                      </div>
                      {sess.user_agent && (
                        <div className="tiny muted truncate" style={{ maxWidth: 340 }} title={sess.user_agent}>
                          {sess.user_agent}
                        </div>
                      )}
                    </td>
                    <td className="num tiny muted nowrap">{sess.ip || '—'}</td>
                    <td className="tiny muted nowrap">{stampLabel(sess.issued_at)}</td>
                    <td className="tiny nowrap">{ago(sess.last_used_at)}</td>
                    <td className="tiny muted nowrap">{stampLabel(sess.expires_at)}</td>
                  </tr>
                ))}
                {!sessions.length && (
                  <tr>
                    <td colSpan={5} className="muted tiny" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
                      No active sessions on record.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {others > 0 && (
          <div className="tiny muted" style={{ marginTop: 'var(--space-2)' }}>
            Individual sessions cannot be ended one at a time yet — use
            {' '}<strong>Sign out of all devices</strong> to end them together.
          </div>
        )}
      </div>
    </Card>
  );
}

/* A readable name for a user-agent string. Deliberately coarse: the point is
   to tell one device apart from another, not to fingerprint the browser. */
function describeAgent(agent) {
  const ua = String(agent || '');
  if (!ua) return 'Unknown device';

  const browser = /Edg\//.test(ua) ? 'Edge'
    : /OPR\/|Opera/.test(ua) ? 'Opera'
      : /Chrome\//.test(ua) ? 'Chrome'
        : /Firefox\//.test(ua) ? 'Firefox'
          : /Safari\//.test(ua) ? 'Safari'
            : '';

  const platform = /iPhone/.test(ua) ? 'iPhone'
    : /iPad/.test(ua) ? 'iPad'
      : /Android/.test(ua) ? 'Android'
        : /Mac OS X|Macintosh/.test(ua) ? 'macOS'
          : /Windows/.test(ua) ? 'Windows'
            : /Linux/.test(ua) ? 'Linux'
              : '';

  if (browser && platform) return `${browser} on ${platform}`;
  return browser || platform || ua.slice(0, 40);
}

function DemoSettings() {
  const toast = useToast();
  const { data: demo, refetch } = useQuery('demo', () => api.demo());
  const [busy, setBusy] = useState(null);

  if (!demo) return null;
  const ws = demo.workspace || {};

  const toggle = async (enabled) => {
    setBusy('toggle');
    try {
      /* switchDemo reloads the page: every cached query belongs to the other
         workspace once the tenant flips. */
      await switchDemo(enabled);
    } catch (e) {
      toast.fail('Could not switch workspace', e.message);
      setBusy(null);
    }
  };

  const rebuild = async () => {
    setBusy('rebuild');
    try {
      await api.rebuildDemo();
      invalidate('dashboard', 'analysis', 'txns', 'workflow', 'coverage', 'recurring');
      await refetch();
      toast.ok('Demo sandbox rebuilt', 'Synthetic statements regenerated from scratch.');
    } catch (e) {
      toast.fail('Rebuild failed', e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      title="Demo Workspace"
      subtitle="A separate synthetic ledger. Nothing you do inside it touches your real statements."
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
        <div>
          <div className="font-semibold small">
            {demo.enabled ? 'Currently viewing the demo workspace' : 'Currently viewing your real ledger'}
          </div>
          <div className="tiny muted">
            {demo.prepared
              ? `Sandbox holds ${count(ws.transactions || 0)} transactions across ${count(ws.accounts || 0)} accounts`
                + (ws.first_month ? ` (${monthLabelLong(ws.first_month)} – ${monthLabelLong(ws.last_month)})` : '')
              : 'The sandbox is built the first time you switch into it.'}
          </div>
        </div>
        <Switch
          checked={Boolean(demo.enabled)}
          disabled={busy === 'toggle'}
          onChange={toggle}
          label={demo.enabled ? 'Demo on' : 'Demo off'}
        />
      </div>

      <div
        style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          gap: 'var(--space-4)', flexWrap: 'wrap',
          marginTop: 'var(--space-4)', paddingTop: 'var(--space-3)',
          borderTop: '1px solid var(--border-subtle)',
        }}
      >
        <div>
          <div className="font-semibold small">Rebuild Demo Data</div>
          <div className="tiny muted">Regenerates the synthetic statements and discards any edits made inside the sandbox.</div>
        </div>
        <ConfirmButton
          size="sm"
          variant="danger"
          disabled={busy === 'rebuild'}
          question="Rebuild the demo sandbox from scratch?"
          confirmLabel="Rebuild"
          onConfirm={rebuild}
        >
          Rebuild Sandbox
        </ConfirmButton>
      </div>
    </Card>
  );
}

function CategoriesManager() {
  const toast = useToast();
  const { data: categories = [], refetch } = useQuery('custom-categories', () => api.customCategories());
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);

  const handleAdd = async () => {
    const clean = name.trim().toLowerCase();
    if (!clean) return;
    setBusy(true);
    try {
      await api.addCategory(clean);
      setName('');
      invalidate('categories', 'custom-categories');
      await refetch();
      toast.ok(`Created custom category: ${clean}`);
    } catch (e) {
      toast.fail('Could not create category', e.message);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (categoryName) => {
    try {
      await api.deleteCategory(categoryName);
      invalidate('categories', 'custom-categories');
      await refetch();
      toast.ok(`Removed ${titleCase(categoryName)}`);
    } catch (e) {
      toast.fail('Could not remove category', e.message);
    }
  };

  return (
    <Card title="Custom Category Taxonomy" subtitle="Define bespoke classification categories stored permanently with your ledger">
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
        <input
          className="input"
          placeholder="New Category Name (e.g. Pet Care, Woodworking)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ flex: '1 1 200px' }}
        />
        <Button variant="primary" busy={busy} disabled={!name.trim()} onClick={handleAdd}>
          Add Category
        </Button>
      </div>

      {categories.length > 0 ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {categories.map((c) => {
            const label = typeof c === 'string' ? c : c.name;
            return (
              <Chip key={label}>
                {titleCase(label)}
                <button
                  type="button"
                  className="link"
                  aria-label={`Remove ${label}`}
                  style={{ marginLeft: 6 }}
                  onClick={() => handleDelete(label)}
                >
                  ×
                </button>
              </Chip>
            );
          })}
        </div>
      ) : (
        <div className="tiny muted">
          No custom categories yet — the {count(30)} built-in categories cover most statements.
        </div>
      )}
    </Card>
  );
}

function DisplaySettings() {
  const [prefs, setPref] = usePrefs();

  return (
    <Card title="Display & Viewport Preferences" subtitle="Local device rendering adjustments">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div className="font-semibold small">UI Animation Effects</div>
            <div className="tiny muted">Smooth keyframe transitions and radiant chart glows.</div>
          </div>
          <Switch
            checked={Boolean(prefs.animate)}
            onChange={(v) => setPref('animate', v)}
            label="Animate"
          />
        </div>

        {PREFS.filter((pref) => pref.key !== 'animate').map((pref) => (
          <div
            key={pref.key}
            style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--space-4)' }}
          >
            <div>
              <div className="font-semibold small">{pref.label}</div>
              <div className="tiny muted">{pref.hint}</div>
            </div>
            {pref.type === 'select' ? (
              <Select
                value={String(prefs[pref.key] ?? pref.fallback)}
                onChange={(v) => setPref(pref.key, v)}
                options={pref.options}
                style={{ minWidth: 170 }}
              />
            ) : (
              <Switch
                checked={Boolean(prefs[pref.key])}
                onChange={(v) => setPref(pref.key, v)}
                label={pref.label}
              />
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
