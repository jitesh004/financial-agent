import React, { useCallback, useEffect, useState } from 'react';
import { api, count, monthLabelLong, switchDemo } from '../lib';
import { useAuth } from '../auth';
import { Callout, Card, Chip, ConfirmButton } from './ui';

/* The Demo switch.
 *
 * Showing this app means showing somebody a complete financial history, and
 * the only complete one to hand is usually your own. This removes that trade:
 * the app is pointed at a generated workspace of its own - real rows, through
 * the real analytics, in a separate account - and pointed back afterwards.
 *
 * Two things stated on screen because both matter: nothing done during a demo
 * can reach the real ledger, and turning the switch off leaves the demo
 * workspace exactly as it was, so the next demo picks up where the last one
 * finished.
 */
export default function DemoSettings() {
  const { user } = useAuth();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    api.demo()
      .then((body) => { setState(body); setError(null); })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  const on = Boolean(state?.enabled ?? user?.demo_mode);

  async function toggle() {
    setBusy('toggle');
    setError(null);
    try {
      // Reloads on success - see switchDemo. Nothing after this line runs,
      // which is why the busy flag is only cleared on the way out.
      await switchDemo(!on);
    } catch (e) {
      setError(e.message);
      setBusy(null);
    }
  }

  async function rebuild() {
    setBusy('rebuild');
    setError(null);
    try {
      await api.rebuildDemo();
      if (on) {
        // The rows every panel is showing were just thrown away and made
        // again, so what is on screen no longer exists.
        window.location.reload();
        return;
      }
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const w = state?.workspace || {};

  return (
    <Card
      title="Demo mode"
      sub="Run the app on generated statements instead of your own"
    >
      <p style={{ color: 'var(--text-2)', fontSize: 13, lineHeight: 1.6,
        margin: '0 0 12px', maxWidth: '76ch' }}>
        Turn this on and every screen reads a demo workspace — a separate
        account holding fourteen months of generated statements, with a salary
        that drifts across month ends, a card bill matched against the bank
        debit that paid it, an EMI against a real loan schedule and one row
        that genuinely needs review. It is not a mock: those are real rows
        going through the real analytics.
      </p>

      {error && <Callout tone="neg">{error}</Callout>}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center',
        flexWrap: 'wrap' }}>
        <button className={`btn ${on ? '' : 'primary'}`} onClick={toggle}
          disabled={busy === 'toggle'}>
          {busy === 'toggle' ? 'Switching…'
            : on ? 'Turn demo off' : 'Turn demo on'}
        </button>
        {on ? <Chip tone="accent">on — showing generated data</Chip>
          : <Chip tone="pos">off — showing your own ledger</Chip>}
        {state?.prepared && w.transactions ? (
          <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
            {count(w.transactions)} rows · {w.accounts} accounts ·{' '}
            {w.first_month && `${monthLabelLong(w.first_month)} → ${monthLabelLong(w.last_month)}`}
          </span>
        ) : null}
      </div>

      {state?.prepared && (
        <div style={{ marginTop: 14, paddingTop: 12,
          borderTop: '1px solid var(--border)', display: 'flex', gap: 10,
          alignItems: 'center', flexWrap: 'wrap' }}>
          <ConfirmButton
            onConfirm={rebuild}
            disabled={busy === 'rebuild'}
            question="Throw the demo data away and generate it again?"
            confirmLabel="Rebuild it"
          >
            {busy === 'rebuild' ? 'Rebuilding…' : 'Rebuild the demo data'}
          </ConfirmButton>
          <span style={{ fontSize: 12, color: 'var(--text-3)', flex: 1,
            minWidth: 240 }}>
            For a workspace a demo has been walked all over. Only ever touches
            the demo workspace — your own ledger is not reachable from here.
          </span>
        </div>
      )}

      <Callout style={{ marginTop: 14 }}>
        Your own data is never copied, moved or changed by this. The switch
        decides which account the app reads; turning it off leaves the demo
        workspace as it was, so the next demo starts where the last one ended.
      </Callout>
    </Card>
  );
}
