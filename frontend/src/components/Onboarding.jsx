import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../lib';
import { useTheme } from '../theme';
import { Callout, ThemeToggle } from './ui';

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
 *
 * Two things this screen is careful about, both of which used to be wrong:
 *
 *   - It is drawn in ONE pass. The details step used to render from the
 *     onboarding payload and then fetch the profile separately, so the fields
 *     visibly filled themselves in a beat after the form appeared.
 *   - It is themed. Every colour here is a token, but the attribute that
 *     selects the dark set was written by the app shell - which renders only
 *     after this screen is done with. A dark-themed app therefore opened on a
 *     white wizard. The theme is applied at boot now (see theme.js), and it is
 *     settable from here rather than only from behind.
 */

const STEPS = [
  ['identity', 'Your details', 'Opens password-protected statements'],
  ['mailbox', 'Your mailbox', 'Finds statements instead of downloading them'],
  ['import', 'First import', 'Reads them, and checks the arithmetic'],
];

export default function Onboarding({ onFinished, onImport }) {
  const [state, setState] = useState(null);
  const [profile, setProfile] = useState(null);
  const [step, setStep] = useState('identity');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [theme, toggleTheme] = useTheme();
  /* Carried from the details step to the next screen rather than shown on the
     one being left: the save advances immediately, so a confirmation rendered
     there would flash for a frame and be gone. */
  const [savedNote, setSavedNote] = useState(null);

  /* Both requests before the first render of the form, so nothing on screen
     changes underneath the person filling it in. A failed profile read is not
     fatal - an empty form is a correct starting point. */
  const load = useCallback(async () => {
    try {
      const [body, saved] = await Promise.all([
        api.onboarding(),
        api.profile().catch(() => ({})),
      ]);
      setState(body);
      setProfile(saved || {});
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

  const index = STEPS.findIndex(([key]) => key === step);

  /* What is actually done, read from the world rather than from how far the
     person has clicked - the server reports each one (see _onboarding_state),
     so a Gmail grant revoked at Google's end stops showing as connected. */
  const done = useMemo(() => ({
    identity: Boolean(state?.identity?.ready),
    mailbox: Boolean(state?.mailbox?.connected),
    import: (state?.import?.transactions || 0) > 0,
  }), [state]);

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

  return (
    <div className="onboarding">
      <div className="auth-theme">
        <ThemeToggle theme={theme} onToggle={toggleTheme} />
      </div>

      <div className="onboarding-panel">
        <header className="onboarding-head">
          <div>
            <h1>Welcome{state.user?.name ? `, ${state.user.name.split(' ')[0]}` : ''}</h1>
            <p>Three short steps — and any of them can wait. Nothing here is
              required to use the app.</p>
          </div>
          <button className="btn onboarding-skip" onClick={() => finish(false)}
            disabled={busy}>
            Skip setup
          </button>
        </header>

        {/* Where you are, in a line. The step list below says what each step
            is; this says how much is left, which is the question a wizard
            most often refuses to answer. */}
        <div className="onboarding-progress" role="presentation">
          <div className="onboarding-progress-fill"
            style={{ width: `${((index + 1) / STEPS.length) * 100}%` }} />
        </div>

        <ol className="onboarding-steps">
          {STEPS.map(([key, label, why], i) => (
            <li key={key}
              className={[
                i === index ? 'current' : '',
                done[key] ? 'done' : '',
                i < index && !done[key] ? 'skipped' : '',
              ].filter(Boolean).join(' ')}>
              <button onClick={() => goTo(key)} title={why}
                aria-current={i === index ? 'step' : undefined}>
                <span className="onboarding-dot">
                  {done[key] ? '✓' : i + 1}
                </span>
                {label}
              </button>
            </li>
          ))}
        </ol>

        {error && <Callout tone="warn">{error}</Callout>}

        {/* Keyed by step so each screen mounts fresh - which is what gives it
            the entry transition, and what stops a form field from carrying a
            value across to the step after it. */}
        <div className="onboarding-body" key={step}>
          {step === 'identity' && (
            <IdentityStep
              profile={profile}
              onSaved={async (result) => { setSavedNote(result); await load(); }}
              onNext={() => goTo('mailbox')}
            />
          )}
          {step === 'mailbox' && (
            <MailboxStep state={state} savedNote={savedNote}
              onNext={() => goTo('import')} />
          )}
          {step === 'import' && (
            <ImportStep state={state} busy={busy}
              onImport={() => finish(true)} onLater={() => finish(false)} />
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------- 1. Details ---------- */

//: A PAN is five letters, four digits, a letter. Checked to HINT, never to
//: block: a malformed one is stored as typed (see models/profile.py) and
//: simply produces no password candidates, and refusing to save it would
//: strand anyone whose card reads differently from what this expects.
const PAN_SHAPE = /^[A-Za-z]{5}[0-9]{4}[A-Za-z]$/;

/* The same fields as the Profile screen, asked here because they are what
   unlocks password-protected statements - and being asked for a PAN out of
   nowhere, later, with no explanation, is alarming in a way that being asked
   during setup with the reason attached is not. */
function IdentityStep({ profile, onSaved, onNext }) {
  const [form, setForm] = useState(() => ({
    full_name: profile?.full_name || '',
    date_of_birth: profile?.date_of_birth || '',
    pan: profile?.pan || '',
    mobile: profile?.mobile || '',
  }));
  const [status, setStatus] = useState(null);
  const [problem, setProblem] = useState(null);
  const firstField = useRef(null);

  useEffect(() => { firstField.current?.focus(); }, []);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const anything = Object.values(form).some((v) => String(v).trim());
  const panOdd = form.pan.trim() && !PAN_SHAPE.test(form.pan.trim());
  const mobileOdd = form.mobile.trim()
    && form.mobile.replace(/\D/g, '').length !== 10;

  async function saveAndContinue() {
    setStatus('saving');
    setProblem(null);
    try {
      const result = await api.saveProfile(form);
      await onSaved(result);
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
        birth, like <code>pank1407</code>. With these filled in, protected PDFs
        open by themselves instead of stopping the import.
      </p>

      <Callout>
        Kept against your account and used only to open files you upload. Never
        sent to any AI model, and never to anyone else.
      </Callout>

      {/* Enter moves on, from any field. A three-field form that makes you
          reach for the mouse to submit is a form that feels slow. */}
      <form className="grid" style={{ gap: 12, marginTop: 14 }}
        onSubmit={(e) => { e.preventDefault(); saveAndContinue(); }}>
        <label className="field">
          <span>Full name, as printed on your statements</span>
          <input type="text" value={form.full_name} onChange={set('full_name')}
            placeholder="e.g. John Adams" ref={firstField} />
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
            {mobileOdd && (
              <span className="field-hint">
                That is {form.mobile.replace(/\D/g, '').length} digits — most
                Indian statement passwords use all ten.
              </span>
            )}
          </label>
        </div>
        <label className="field">
          <span>PAN — for mutual fund and demat statements</span>
          <input type="text" value={form.pan} onChange={set('pan')}
            placeholder="ABCDE1234F" style={{ textTransform: 'uppercase' }} />
          {panOdd && (
            <span className="field-hint">
              A PAN reads as five letters, four digits and a letter. Saved
              either way — it just will not unlock anything.
            </span>
          )}
        </label>

        {problem && <Callout tone="warn" style={{ marginTop: 12 }}>{problem}</Callout>}

        <footer className="onboarding-actions">
          <button className="btn primary" type="submit"
            disabled={status === 'saving'}>
            {status === 'saving' ? 'Saving…' : 'Save and continue'}
          </button>
          <button className="btn" type="button" onClick={onNext}>
            {anything ? 'Continue without saving' : 'I’ll add these later'}
          </button>
        </footer>
      </form>
    </section>
  );
}

/* ---------- 2. Mailbox ---------- */

function MailboxStep({ state, savedNote, onNext }) {
  const connected = state.mailbox.connected;
  const available = state.mailbox.available;

  return (
    <section className="onboarding-step">
      {/* Confirmation of the step just finished, in a form that says what it
          bought: a candidate count is the one honest measure of whether those
          details will actually open anything. */}
      {savedNote?.password_candidates > 0 && (
        <Callout tone="pos">
          <strong>Your details are saved.</strong> Protected PDFs will be tried
          against {savedNote.password_candidates} password
          {savedNote.password_candidates === 1 ? '' : 's'} built from them —
          none of which leaves your account.
        </Callout>
      )}

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
          <p className="onboarding-aside">
            Google will ask on its own page, and bring you back here.
          </p>
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
