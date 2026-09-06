/* Settings: the model switch, your account, the demo, categories and display.
 *
 * Ordered by what a person arrives here to do. The model switch is first
 * because it is the one setting that decides whether this app ever spends
 * money; the account section is next because "what does this thing have of
 * mine, and can I take it back?" is the question people come to Settings with,
 * rather than the one they browse to.
 *
 * Categories are stored with the rest of your decisions and survive a
 * re-parse. Display preferences live in this browser: they are worthless to
 * anyone else, differ legitimately between a laptop and a phone, and a round
 * trip to save "compact rows" would be absurd.
 */

import React, { useState } from 'react';
import { api, switchDemo } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { useAuth } from '../core/auth';
import { useToast } from '../core/toast';
import { usePrefs, PREFS } from '../core/prefs';
import { count, dateLabel, monthLabelLong, titleCase } from '../core/format';
import { Button, Callout, Card, Chip, ConfirmButton, Select, Stat, Switch, Table } from '../ui';
import JobProgress from '../ui/JobProgress';

export default function Settings() {
  return (
    <>
      <div>
        <h2 className="h2">Settings</h2>
        <p className="lead">
          Categories are stored with your other decisions and survive a re-parse. Display
          options are per-browser.
        </p>
      </div>
      <ModelSettings />
      <AccountSettings />
      <DemoSettings />
      <Categories />
      <Display />
    </>
  );
}

/* ── the switch that decides whether this app spends money ───────────────── */

function ModelSettings() {
  const toast = useToast();
  const { data: settings, refetch } = useQuery('settings', () => api.settings());
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);

  if (!settings) return null;
  const pending = settings.uncategorized_count || 0;
  const done = job && !job.active && job.status === 'complete';

  const toggle = async (next) => {
    try { await api.saveSettings({ use_llm: next }); refetch(); }
    catch (e) { toast.fail('That setting was not saved', e.message); }
  };

  const run = async () => {
    setBusy(true);
    try {
      const { job_id: id } = await api.runCategorize();
      for (;;) {
        // eslint-disable-next-line no-await-in-loop
        const current = await api.job(id).catch(() => null);
        if (!current) break;
        setJob(current);
        if (!current.active) break;
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => { setTimeout(r, 800); });
      }
      // The ledger every other screen reads has just changed underneath it.
      invalidate('dashboard', 'analysis', 'txns', 'workflow', 'settings');
      refetch();
    } catch (e) {
      toast.fail('The run did not finish', e.message);
    } finally { setBusy(false); }
  };

  return (
    <Card
      title="Model categorisation"
      sub={settings.llm_configured
        ? `${settings.llm_provider} configured` : 'no provider configured'}
    >
      <p className="lead" style={{ marginBottom: 12 }}>
        Rules and the merchant cache categorise most rows without a model. What is left
        can go to a language model, which spends a metered budget — tokens on a paid
        provider, or a capped number of requests per day on a free one — so this is off
        until you turn it on, and imports never switch it on for you.
      </p>

      {!settings.llm_configured && (
        <Callout tone="warn">
          No API key is configured, so there is nothing to call. Add one to the
          server&apos;s <code>.env</code> and restart the API.
        </Callout>
      )}

      <div className="list-row" style={{ borderTop: '1px solid var(--line)' }}>
        <div className="grow">
          <div className="list-title">Use a model for rows the rules cannot place</div>
          <div className="list-sub">
            Off by default and stored on the server, not in this browser: the API is what
            decides whether a model is called.
          </div>
        </div>
        <Switch
          checked={Boolean(settings.use_llm)}
          disabled={!settings.llm_configured}
          onChange={toggle}
          label="Use a model for rows the rules cannot place"
        />
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <Chip tone={pending ? 'warn' : 'pos'}>
          {pending} uncategorised row{pending === 1 ? '' : 's'}
        </Chip>
        <Button variant="primary" busy={busy}
          disabled={!settings.use_llm || !pending} onClick={run}>
          Categorise {pending} row{pending === 1 ? '' : 's'}
        </Button>
        {!settings.use_llm && <span className="tiny dim">Turn the switch on first.</span>}
      </div>

      {job && <div style={{ marginTop: 14 }}><JobProgress job={job} title="Categorising" /></div>}

      {done && job.result && (
        <Callout tone="pos" style={{ marginTop: 12 }}>
          <strong>{job.result.updated} of {job.result.considered} categorised.</strong>{' '}
          {job.result.changed_from_cache} came from the merchant cache and{' '}
          {job.result.changed_from_model} from the model.
          {job.result.still_uncategorized > 0 && (
            <> {job.result.still_uncategorized} could not be placed and stay in the review
              queue.</>
          )} Every screen has been refreshed.
        </Callout>
      )}
    </Card>
  );
}

/* ── the account ─────────────────────────────────────────────────────────── */

/* What this app is allowed to reach, which devices hold a session, and how to
   leave. All three are here because they answer the same question - "what does
   this thing have of mine, and can I take it back?" - and a privacy promise
   you cannot act on from inside the product is not much of a promise. */
function AccountSettings() {
  const { user, refresh } = useAuth();
  const toast = useToast();
  const gmail = useQuery('gmail-status', () => api.gmailStatus());
  const sessions = useQuery('sessions', () => api.activeSessions());
  const [confirmEmail, setConfirmEmail] = useState('');

  if (!user) return null;
  const rows = sessions.data?.sessions || [];

  return (
    <>
      <Card title="Your mailbox" sub={gmail.data?.connected ? 'Connected' : 'Not connected'}>
        <p className="lead" style={{ marginBottom: 12 }}>
          Read-only access to Gmail, so statements are found rather than downloaded by
          hand. Separate from signing in, and safe to withdraw: everything already
          imported stays exactly where it is.
        </p>
        {gmail.data?.connected ? (
          <ConfirmButton
            question="Disconnect Gmail? Imported statements are kept."
            confirmLabel="Disconnect"
            onConfirm={async () => {
              try {
                await api.gmailDisconnect();
                gmail.refetch();
                toast.ok('Gmail disconnected', 'The grant has been revoked at Google too.');
              } catch (e) { toast.fail('It could not be disconnected', e.message); }
            }}
          >
            Disconnect Gmail
          </ConfirmButton>
        ) : gmail.data?.available ? (
          <Button variant="primary" icon="mail" onClick={() => api.gmailConnect()}>
            Connect Gmail
          </Button>
        ) : (
          <Callout tone="warn">
            {gmail.data?.setup_hint || 'Mailbox import is not configured on this server.'}
          </Callout>
        )}
      </Card>

      <Card title="Signed in on" sub={`${rows.length} device(s)`} pad={false}>
        <p className="lead" style={{ padding: '12px 16px 0' }}>
          Every browser holding a live session. Sessions are stored on the server, which
          is what makes ending one from here actually end it.
        </p>
        <Table>
          <thead>
            <tr><th>Device</th><th>Last used</th><th>Signed in</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={`${s.issued_at}-${s.user_agent}`}>
                <td>
                  <div className="truncate" style={{ maxWidth: 320 }} title={s.user_agent}>
                    {describe(s.user_agent)}
                  </div>
                </td>
                <td>{dateLabel(s.last_used_at)}</td>
                <td>{dateLabel(s.issued_at)}</td>
                <td>{s.current && <Chip tone="acc">This one</Chip>}</td>
              </tr>
            ))}
          </tbody>
        </Table>
        <div style={{ padding: 14 }}>
          <ConfirmButton
            question="Sign out everywhere, including here?"
            confirmLabel="Sign out everywhere"
            onConfirm={async () => {
              try { await api.logoutEverywhere(); } finally { await refresh(); }
            }}
          >
            Sign out on every device
          </ConfirmButton>
        </div>
      </Card>

      <Card title="Delete your account" sub="Irreversible">
        <p className="lead" style={{ marginBottom: 12 }}>
          Removes every statement, transaction, correction, claim, dashboard and uploaded
          file belonging to <strong>{user.email}</strong>. There is no undo and no copy
          kept.
        </p>
        <div className="row">
          <input
            value={confirmEmail}
            onChange={(e) => setConfirmEmail(e.target.value)}
            placeholder="Type your email address to confirm"
            style={{ flex: '1 1 260px' }}
          />
          <ConfirmButton
            variant="danger"
            question="Delete everything, permanently?"
            confirmLabel="Delete my account"
            disabled={confirmEmail.trim().toLowerCase() !== user.email.toLowerCase()}
            onConfirm={async () => {
              try { await api.deleteAccount(confirmEmail); await refresh(); }
              catch (e) { toast.fail('The account was not deleted', e.message); }
            }}
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

/* ── demo ────────────────────────────────────────────────────────────────── */

/* Showing this app means showing somebody a complete financial history, and
   the only complete one to hand is usually your own. This removes that trade:
   the app is pointed at a generated workspace of its own - real rows, through
   the real analytics, in a separate account - and pointed back afterwards. */
function DemoSettings() {
  const { user } = useAuth();
  const toast = useToast();
  const { data, refetch } = useQuery('demo', () => api.demo());
  const [busy, setBusy] = useState(null);

  const on = Boolean(data?.enabled ?? user?.demo_mode);
  const w = data?.workspace || {};

  return (
    <Card title="Demo mode" sub="Run the app on generated statements instead of your own">
      <p className="lead" style={{ marginBottom: 12 }}>
        Turn this on and every screen reads a demo workspace — a separate account holding
        fourteen months of generated statements, with a salary that drifts across month
        ends, a card bill matched against the bank debit that paid it, an EMI against a
        real loan schedule and one row that genuinely needs review. It is not a mock:
        those are real rows going through the real analytics.
      </p>

      <div className="row">
        <Button
          variant={on ? '' : 'primary'}
          busy={busy === 'toggle'}
          onClick={async () => {
            setBusy('toggle');
            try { await switchDemo(!on); } // reloads on success
            catch (e) { toast.fail('The switch did not flip', e.message); setBusy(null); }
          }}
        >
          {on ? 'Turn demo off' : 'Turn demo on'}
        </Button>
        {on ? <Chip tone="acc">on — showing generated data</Chip>
          : <Chip tone="pos">off — showing your own ledger</Chip>}
        {data?.prepared && w.transactions ? (
          <span className="tiny dim">
            {count(w.transactions)} rows · {w.accounts} accounts
            {w.first_month && ` · ${monthLabelLong(w.first_month)} → ${monthLabelLong(w.last_month)}`}
          </span>
        ) : null}
      </div>

      {data?.prepared && (
        <div className="row" style={{
          marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line)',
        }}>
          <ConfirmButton
            disabled={busy === 'rebuild'}
            question="Throw the demo data away and generate it again?"
            confirmLabel="Rebuild it"
            onConfirm={async () => {
              setBusy('rebuild');
              try {
                await api.rebuildDemo();
                // The rows every screen is showing were just thrown away and
                // made again, so what is on screen no longer exists.
                if (on) { window.location.reload(); return; }
                refetch();
              } catch (e) { toast.fail('The rebuild failed', e.message); }
              finally { setBusy(null); }
            }}
          >
            Rebuild the demo data
          </ConfirmButton>
          <span className="tiny dim" style={{ flex: 1, minWidth: 240 }}>
            For a workspace a demo has been walked all over. Only ever touches the demo
            workspace — your own ledger is not reachable from here.
          </span>
        </div>
      )}

      <Callout style={{ marginTop: 14 }}>
        Your own data is never copied, moved or changed by this. The switch decides which
        account the app reads; turning it off leaves the demo workspace as it was, so the
        next demo starts where the last one ended.
      </Callout>
    </Card>
  );
}

/* ── categories ──────────────────────────────────────────────────────────── */

function Categories() {
  const toast = useToast();
  const all = useQuery('categories', () => api.categories());
  /* Asked for directly rather than assumed from position. An earlier version
     took the first 30 entries of /api/categories as "built-in", a count that
     happened to match the built-in list at the time but would silently
     misclassify everything the moment that list changed. */
  const custom = useQuery('categories-custom', () => api.customCategories());
  const [name, setName] = useState('');

  const list = all.data || [];
  const customSet = new Set(custom.data || []);
  const mine = list.filter((c) => customSet.has(c));
  const builtin = list.filter((c) => !customSet.has(c));

  async function add(e) {
    e.preventDefault();
    const clean = name.trim().toLowerCase().replace(/\s+/g, '_');
    if (!clean) return;
    try {
      await api.addCategory(clean);
      setName('');
      invalidate('categories');
      toast.ok(`Added “${titleCase(clean)}”`,
        'It is now available everywhere a category can be chosen.');
    } catch (e2) { toast.fail('It could not be added', e2.message); }
  }

  return (
    <>
      <div className="grid cols-2">
        <Stat label="Categories available" value={String(list.length)} />
        <Stat label="Added by you" value={String(mine.length)} />
      </div>

      <Card title="Your categories" sub="Available anywhere a category is chosen">
        <form onSubmit={add} className="row" style={{ marginBottom: 14 }}>
          <input value={name} placeholder="e.g. pets, gifts_received, side_project"
            onChange={(e) => setName(e.target.value)} style={{ flex: 1 }} />
          <Button variant="primary" type="submit" disabled={!name.trim()}>Add category</Button>
        </form>

        {!mine.length && (
          <div className="dim small">
            None yet. The {builtin.length || list.length} built-in categories cover most
            things; add your own when they do not.
          </div>
        )}
        <div className="row tight">
          {mine.map((c) => (
            <span key={c} className="row tight" style={{ display: 'inline-flex' }}>
              <Chip tone="acc">{titleCase(c)}</Chip>
              <ConfirmButton
                size="xs" className="ghost"
                title={`Remove ${titleCase(c)}`}
                question={`Remove “${titleCase(c)}”? Transactions using it become uncategorised.`}
                confirmLabel="Remove"
                onConfirm={async () => {
                  try { await api.deleteCategory(c); invalidate('categories'); }
                  catch (e) { toast.fail('It could not be removed', e.message); }
                }}
              >
                ×
              </ConfirmButton>
            </span>
          ))}
        </div>
      </Card>

      <Card title="Built-in categories" sub={`${builtin.length} always available`}>
        <div className="row tight">
          {builtin.map((c) => <Chip key={c}>{titleCase(c)}</Chip>)}
        </div>
      </Card>
    </>
  );
}

/* ── display ─────────────────────────────────────────────────────────────── */

function Display() {
  const [prefs, setPref] = usePrefs();
  return (
    <Card title="Display" sub="Remembered in this browser only" pad>
      <div className="rule-list">
        {PREFS.map((p) => (
          <div key={p.key} className="list-row">
            <div className="grow">
              <div className="list-title">{p.label}</div>
              <div className="list-sub">{p.hint}</div>
            </div>
            {p.type === 'toggle' ? (
              <Switch checked={Boolean(prefs[p.key])} label={p.label}
                onChange={(v) => setPref(p.key, v)} />
            ) : (
              <Select value={prefs[p.key]} onChange={(v) => setPref(p.key, v)}
                options={p.options} aria-label={p.label} />
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
