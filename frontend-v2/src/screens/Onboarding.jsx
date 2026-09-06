/* First run.
 *
 * Three steps, and every one of them is skippable. That is deliberate: the app
 * works with no profile, no mailbox and no statements, so a wizard that will
 * not let you out would be inventing a requirement the software does not have.
 * What each step buys you is stated on it, and the button that skips says what
 * you are giving up rather than just "Skip".
 *
 * Progress lives on the server, not in this component: somebody who closes the
 * tab half way through and signs in from another machine should land where
 * they left off, and the step only ever moves forward, so revisiting a screen
 * to fix a typo does not un-onboard them.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../core/api';
import { useTheme } from '../core/theme';
import { count } from '../core/format';
import {
  Button, Callout, Field, IconButton, Loading, ProgressBar,
} from '../ui';
import { Logo } from '../ui/icons';

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
  /* Carried to the next screen rather than shown on the one being left: the
     save advances immediately, so a confirmation rendered there would flash
     for a frame and be gone. */
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
    } catch (e) { setError(e.message); }
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
    } catch (e) { setError(e.message); setBusy(false); }
  }, [onFinished, onImport]);

  const index = STEPS.findIndex(([key]) => key === step);

  /* What is actually done, read from the world rather than from how far
     somebody has clicked - so a Gmail grant revoked at Google's end stops
     showing as connected. */
  const done = useMemo(() => ({
    identity: Boolean(state?.identity?.ready),
    mailbox: Boolean(state?.mailbox?.connected),
    import: (state?.import?.transactions || 0) > 0,
  }), [state]);

  if (!state) {
    return (
      <div className="gate">
        <div className="gate-panel">
          {error ? <Callout tone="warn">{error}</Callout> : <Loading />}
        </div>
      </div>
    );
  }

  return (
    <div className="gate">
      <div className="gate-corner">
        <IconButton icon={theme === 'dark' ? 'sun' : 'moon'} label="Toggle theme"
          className="ghost" onClick={toggleTheme} />
      </div>

      <div className="gate-panel wide">
        <div className="row" style={{ alignItems: 'flex-start', marginBottom: 14 }}>
          <div className="grow" style={{ flex: 1 }}>
            <div className="gate-brand" style={{ marginBottom: 8 }}><Logo size={22} /> Prism</div>
            <h1 className="h1">
              Welcome{state.user?.name ? `, ${state.user.name.split(' ')[0]}` : ''}
            </h1>
            <p className="lead">
              Three short steps — and any of them can wait. Nothing here is required to
              use the app.
            </p>
          </div>
          <Button onClick={() => finish(false)} disabled={busy}>Skip setup</Button>
        </div>

        <ProgressBar value={((index + 1) / STEPS.length) * 100} />

        <div className="steps" style={{ margin: '14px 0 18px' }}>
          {STEPS.map(([key, label, why], i) => (
            <React.Fragment key={key}>
              {i > 0 && <span className="step-link" aria-hidden="true" />}
              <button
                type="button"
                className={`step ${i === index ? 'on' : ''} ${done[key] ? 'done' : ''}`}
                onClick={() => goTo(key)}
                title={why}
                aria-current={i === index ? 'step' : undefined}
              >
                <span className="step-dot">{done[key] ? '✓' : i + 1}</span>
                {label}
              </button>
            </React.Fragment>
          ))}
        </div>

        {error && <Callout tone="warn">{error}</Callout>}

        {/* Keyed by step so each screen mounts fresh - which gives it the entry
            transition, and stops a form field carrying a value across. */}
        <div key={step} style={{ animation: 'rise var(--t) var(--e-out)' }}>
          {step === 'identity' && (
            <IdentityStep
              profile={profile}
              onSaved={async (result) => { setSavedNote(result); await load(); }}
              onNext={() => goTo('mailbox')}
            />
          )}
          {step === 'mailbox' && (
            <MailboxStep state={state} savedNote={savedNote} onNext={() => goTo('import')} />
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

/* ---------- 1. details ---------- */

//: A PAN is five letters, four digits, a letter. Checked to HINT, never to
//: block: a malformed one is stored as typed and simply produces no password
//: candidates, and refusing to save it would strand anyone whose card reads
//: differently from what this expects.
const PAN_SHAPE = /^[A-Za-z]{5}[0-9]{4}[A-Za-z]$/;

function IdentityStep({ profile, onSaved, onNext }) {
  const [form, setForm] = useState(() => ({
    full_name: profile?.full_name || '',
    date_of_birth: profile?.date_of_birth || '',
    pan: profile?.pan || '',
    mobile: profile?.mobile || '',
  }));
  const [status, setStatus] = useState(null);
  const [problem, setProblem] = useState(null);
  const first = useRef(null);

  useEffect(() => { first.current?.focus(); }, []);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const anything = Object.values(form).some((v) => String(v).trim());
  const panOdd = form.pan.trim() && !PAN_SHAPE.test(form.pan.trim());
  const mobileOdd = form.mobile.trim() && form.mobile.replace(/\D/g, '').length !== 10;

  async function saveAndContinue(e) {
    e?.preventDefault();
    setStatus('saving');
    setProblem(null);
    try {
      const result = await api.saveProfile(form);
      await onSaved(result);
      onNext();
    } catch (err) { setProblem(err.message); setStatus(null); }
  }

  return (
    <section className="col">
      <h2 className="h2">Your details</h2>
      <p className="lead">
        Indian banks build statement passwords out of your own details — the classic
        format is the first four letters of your name plus your date of birth, like{' '}
        <code>pank1407</code>. With these filled in, protected PDFs open by themselves
        instead of stopping the import.
      </p>

      <Callout tone="acc">
        Kept against your account and used only to open files you upload. Never sent to
        any model, and never to anyone else.
      </Callout>

      {/* Enter moves on, from any field. A three-field form that makes you
          reach for the mouse to submit is a form that feels slow. */}
      <form className="col" onSubmit={saveAndContinue}>
        <Field label="Full name, as printed on your statements">
          <input type="text" value={form.full_name} onChange={set('full_name')}
            placeholder="e.g. John Adams" ref={first} />
        </Field>
        <div className="grid cols-2">
          <Field label="Date of birth">
            <input type="date" value={form.date_of_birth} onChange={set('date_of_birth')} />
          </Field>
          <Field
            label="Mobile number"
            hint={mobileOdd
              ? `That is ${form.mobile.replace(/\D/g, '').length} digits — most Indian `
                + 'statement passwords use all ten.'
              : undefined}
          >
            <input type="text" inputMode="numeric" value={form.mobile}
              onChange={set('mobile')} placeholder="10 digits" />
          </Field>
        </div>
        <Field
          label="PAN — for mutual fund and demat statements"
          hint={panOdd
            ? 'A PAN reads as five letters, four digits and a letter. Saved either way '
              + '— it just will not unlock anything.'
            : undefined}
        >
          <input type="text" value={form.pan} onChange={set('pan')}
            placeholder="ABCDE1234F" style={{ textTransform: 'uppercase' }} />
        </Field>

        {problem && <Callout tone="warn">{problem}</Callout>}

        <div className="row" style={{ marginTop: 4 }}>
          <Button variant="primary" type="submit" busy={status === 'saving'}>
            Save and continue
          </Button>
          <Button type="button" onClick={onNext}>
            {anything ? 'Continue without saving' : 'I’ll add these later'}
          </Button>
        </div>
      </form>
    </section>
  );
}

/* ---------- 2. mailbox ---------- */

function MailboxStep({ state, savedNote, onNext }) {
  const { connected, available } = state.mailbox;

  return (
    <section className="col">
      {/* Confirmation of the step just finished, in a form that says what it
          bought: a candidate count is the one honest measure of whether those
          details will actually open anything. */}
      {savedNote?.password_candidates > 0 && (
        <Callout tone="pos">
          <strong>Your details are saved.</strong> Protected PDFs will be tried against{' '}
          {savedNote.password_candidates} password
          {savedNote.password_candidates === 1 ? '' : 's'} built from them — none of
          which leaves your account.
        </Callout>
      )}

      <h2 className="h2">Read statements from your mailbox</h2>
      <p className="lead">
        Rather than downloading every statement yourself, Prism can find the bank and
        card emails already sitting in your Gmail and pull the PDFs out of them.
      </p>

      {connected ? (
        <Callout tone="pos">
          <strong>Gmail is connected.</strong> You can scan for statements on the next
          step.
        </Callout>
      ) : available ? (
        <>
          <ul className="col" style={{ gap: 8 }}>
            <li className="small muted">
              <strong style={{ color: 'var(--text)' }}>Read-only.</strong> The permission
              is <code>gmail.readonly</code> — it can read and download, and can never
              send, delete or modify a message.
            </li>
            <li className="small muted">
              <strong style={{ color: 'var(--text)' }}>Nothing downloads until you say
              so.</strong> A scan lists what it found; you tick the statements you want.
            </li>
            <li className="small muted">
              <strong style={{ color: 'var(--text)' }}>Separate from signing in.</strong>{' '}
              This is its own grant, and you can withdraw it at any time from Settings.
            </li>
          </ul>
          <div className="row">
            <Button variant="primary" icon="mail" onClick={() => api.gmailConnect()}>
              Connect Gmail
            </Button>
            <Button onClick={onNext}>Not now — I’ll upload files myself</Button>
          </div>
          <p className="tiny dim">Google will ask on its own page, and bring you back here.</p>
        </>
      ) : (
        <Callout tone="warn">
          Mailbox import is not configured on this server. You can still upload
          statements from your computer.
        </Callout>
      )}

      {(connected || !available) && (
        <div className="row">
          <Button variant="primary" onClick={onNext}>Continue</Button>
        </div>
      )}
    </section>
  );
}

/* ---------- 3. first import ---------- */

function ImportStep({ state, busy, onImport, onLater }) {
  const already = state.import.transactions > 0;
  return (
    <section className="col">
      <h2 className="h2">Bring in your statements</h2>
      <p className="lead">
        Scan your mailbox or add files from this computer — both start in the same
        place. Everything found is listed for you to review before a single figure is
        counted.
      </p>

      {already ? (
        <Callout tone="pos">
          <strong>{count(state.import.transactions)} transactions are already in your
          ledger.</strong> You are set up.
        </Callout>
      ) : (
        <ul className="col" style={{ gap: 8 }}>
          <li className="small muted">
            <strong style={{ color: 'var(--text)' }}>Any format.</strong> PDF, XLSX, CSV,
            DOCX — detected by content, not by the file extension.
          </li>
          <li className="small muted">
            <strong style={{ color: 'var(--text)' }}>Checked, not assumed.</strong>{' '}
            Opening balance plus credits minus debits has to equal the closing balance
            your bank printed, or the file goes back through extraction.
          </li>
          <li className="small muted">
            <strong style={{ color: 'var(--text)' }}>Counted once.</strong> A card bill
            paid from your bank account appears on both statements; the matching pair is
            counted as one movement of money, not two.
          </li>
        </ul>
      )}

      <div className="row">
        <Button variant="primary" icon="upload" onClick={onImport} busy={busy}>
          Import statements
        </Button>
        <Button onClick={onLater} disabled={busy}>
          {already ? 'Go to my dashboard' : 'I’ll do this later'}
        </Button>
      </div>
    </section>
  );
}
