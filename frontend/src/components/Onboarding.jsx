import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../lib';
import { Callout } from './ui';

/* First run.
 *
 * Three steps, and every one of them is skippable. That is deliberate: the app
 * works with no profile, no mailbox and no statements, so a wizard that will
 * not let you out would be inventing a requirement the software does not have.
 * What the steps buy you is stated on each screen, and the button that skips
 * says what you are giving up rather than just "Skip".
 *
 * Progress lives on the server (`users.onboarding_step`), not in this
 * component: someone who closes the tab half-way through and signs in from a
 * different machine should land where they left off, and the step only ever
 * moves forward so revisiting a screen to fix a typo does not un-onboard them.
 */

const STEPS = [
  ['identity', 'Your details'],
  ['mailbox', 'Your mailbox'],
  ['import', 'First import'],
];

export default function Onboarding({ onFinished, onImport }) {
  const [state, setState] = useState(null);
  const [step, setStep] = useState('identity');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const body = await api.onboarding();
      setState(body);
      setStep(body.step === 'done' ? 'import' : body.step);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const goTo = useCallback(async (next) => {
    setStep(next);
    setError(null);
    try { setState(await api.onboardingStep(next)); } catch { /* cosmetic */ }
  }, []);

  const finish = useCallback(async (thenImport = false) => {
    setBusy(true);
    setError(null);
    try {
      const body = await api.onboardingComplete();
      onFinished?.(body.user);
      if (thenImport) onImport?.();
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }, [onFinished, onImport]);

  if (!state) {
    return (
      <div className="onboarding">
        <div className="onboarding-panel">
          {error ? <Callout tone="warn">{error}</Callout>
            : <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <div className="spinner" /> Loading…
            </div>}
        </div>
      </div>
    );
  }

  const index = STEPS.findIndex(([key]) => key === step);

  return (
    <div className="onboarding">
      <div className="onboarding-panel">
        <header className="onboarding-head">
          <div>
            <h1>Welcome{state.user?.name ? `, ${state.user.name.split(' ')[0]}` : ''}</h1>
            <p>Three short steps. You can skip any of them and come back later
              from Settings.</p>
          </div>
          <button className="btn onboarding-skip" onClick={() => finish(false)}
            disabled={busy}>
            Skip setup
          </button>
        </header>

        <ol className="onboarding-steps">
          {STEPS.map(([key, label], i) => (
            <li key={key}
              className={i === index ? 'current' : i < index ? 'done' : ''}>
              <button onClick={() => goTo(key)}>
                <span className="onboarding-dot">{i < index ? '✓' : i + 1}</span>
                {label}
              </button>
            </li>
          ))}
        </ol>

        {error && <Callout tone="warn">{error}</Callout>}

        {step === 'identity' && (
          <IdentityStep state={state} onSaved={load}
            onNext={() => goTo('mailbox')} />
        )}
        {step === 'mailbox' && (
          <MailboxStep state={state} onNext={() => goTo('import')} />
        )}
        {step === 'import' && (
          <ImportStep state={state} busy={busy}
            onImport={() => finish(true)} onLater={() => finish(false)} />
        )}
      </div>
    </div>
  );
}

/* ---------- 1. Details ---------- */

/* The same fields as the Profile screen, asked here because they are what
   unlocks password-protected statements - and being asked for a PAN out of
   nowhere, later, with no explanation, is alarming in a way that being asked
   during setup with the reason attached is not. */
function IdentityStep({ state, onSaved, onNext }) {
  const [form, setForm] = useState({
    full_name: state.identity.full_name || '',
    date_of_birth: '', pan: '', mobile: '',
  });
  const [status, setStatus] = useState(null);
  const [problem, setProblem] = useState(null);

  useEffect(() => {
    api.profile().then((p) => setForm({
      full_name: p.full_name || '',
      date_of_birth: p.date_of_birth || '',
      pan: p.pan || '',
      mobile: p.mobile || '',
    })).catch(() => {});
  }, []);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function saveAndContinue() {
    setStatus('saving');
    setProblem(null);
    try {
      await api.saveProfile(form);
      await onSaved();
      onNext();
    } catch (e) {
      setProblem(e.message);
      setStatus(null);
    }
  }

  return (
    <section className="onboarding-step">
      <h2>Your details</h2>
      <p className="onboarding-lead">
        Indian banks build statement passwords out of your own details — the
        classic format is the first four letters of your name plus your date of
        birth, like <code>jite0602</code>. With these filled in, protected PDFs
        open by themselves instead of stopping the import.
      </p>

      <Callout>
        Kept against your account and used only to open files you upload. Never
        sent to any AI model, and never to anyone else.
      </Callout>

      <div className="grid" style={{ gap: 12, marginTop: 14 }}>
        <label className="field">
          <span>Full name, as printed on your statements</span>
          <input type="text" value={form.full_name} onChange={set('full_name')}
            placeholder="e.g. Jitesh Sharma" autoFocus />
        </label>
        <div className="grid cols-2" style={{ gap: 12 }}>
          <label className="field">
            <span>Date of birth</span>
            <input type="date" value={form.date_of_birth}
              onChange={set('date_of_birth')} />
          </label>
          <label className="field">
            <span>Mobile number</span>
            <input type="text" inputMode="numeric" value={form.mobile}
              onChange={set('mobile')} placeholder="10 digits" />
          </label>
        </div>
        <label className="field">
          <span>PAN — for mutual fund and demat statements</span>
          <input type="text" value={form.pan} onChange={set('pan')}
            placeholder="ABCDE1234F" style={{ textTransform: 'uppercase' }} />
        </label>
      </div>

      {problem && <Callout tone="warn" style={{ marginTop: 12 }}>{problem}</Callout>}

      <footer className="onboarding-actions">
        <button className="btn primary" onClick={saveAndContinue}
          disabled={status === 'saving'}>
          {status === 'saving' ? 'Saving…' : 'Save and continue'}
        </button>
        <button className="btn" onClick={onNext}>
          I’ll add these later
        </button>
      </footer>
    </section>
  );
}

/* ---------- 2. Mailbox ---------- */

function MailboxStep({ state, onNext }) {
  const connected = state.mailbox.connected;
  const available = state.mailbox.available;

  return (
    <section className="onboarding-step">
      <h2>Read statements from your mailbox</h2>
      <p className="onboarding-lead">
        Rather than downloading every statement yourself, Prism can find the
        bank and card emails already sitting in your Gmail and pull the PDFs
        out of them.
      </p>

      {connected ? (
        <Callout tone="pos">
          <strong>Gmail is connected.</strong> You can scan for statements on
          the next step.
        </Callout>
      ) : available ? (
        <>
          <ul className="onboarding-points">
            <li><strong>Read-only.</strong> The permission is
              <code> gmail.readonly</code> — it can read and download, and can
              never send, delete or modify a message.</li>
            <li><strong>Nothing downloads until you say so.</strong> A scan
              lists what it found; you tick the statements you want.</li>
            <li><strong>Separate from signing in.</strong> This is its own
              grant, and you can withdraw it at any time from Settings.</li>
          </ul>
          <footer className="onboarding-actions">
            <button className="btn primary" onClick={() => api.gmailConnect()}>
              Connect Gmail
            </button>
            <button className="btn" onClick={onNext}>
              Not now — I’ll upload files myself
            </button>
          </footer>
        </>
      ) : (
        <Callout tone="warn">
          Mailbox import is not configured on this server. You can still upload
          statements from your computer.
        </Callout>
      )}

      {(connected || !available) && (
        <footer className="onboarding-actions">
          <button className="btn primary" onClick={onNext}>Continue</button>
        </footer>
      )}
    </section>
  );
}

/* ---------- 3. First import ---------- */

function ImportStep({ state, busy, onImport, onLater }) {
  const already = state.import.transactions > 0;

  return (
    <section className="onboarding-step">
      <h2>Bring in your statements</h2>
      <p className="onboarding-lead">
        Scan your mailbox or add files from this computer — both start in the
        same place. Everything found is listed for you to review before a single
        figure is counted.
      </p>

      {already ? (
        <Callout tone="pos">
          <strong>{state.import.transactions.toLocaleString('en-IN')} transactions
          are already in your ledger.</strong> You are set up.
        </Callout>
      ) : (
        <ul className="onboarding-points">
          <li><strong>Any format.</strong> PDF, XLSX, CSV, DOCX — detected by
            content, not by the file extension.</li>
          <li><strong>Checked, not assumed.</strong> Opening balance plus
            credits minus debits has to equal the closing balance your bank
            printed, or the file goes back through extraction.</li>
          <li><strong>Counted once.</strong> A card bill paid from your bank
            account appears on both statements; the matching pair is counted as
            one movement of money, not two.</li>
        </ul>
      )}

      <footer className="onboarding-actions">
        <button className="btn primary" onClick={onImport} disabled={busy}>
          {busy ? 'Finishing…' : 'Import statements'}
        </button>
        <button className="btn" onClick={onLater} disabled={busy}>
          {already ? 'Go to my dashboard' : 'I’ll do this later'}
        </button>
      </footer>
    </section>
  );
}
