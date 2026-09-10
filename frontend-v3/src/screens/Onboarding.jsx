/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Onboarding Wizard
   ──────────────────────────────────────────────────────────────────────── */

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
  ['import', 'First import', 'Reads them, and checks arithmetic'],
];

export default function Onboarding({ onFinished, onImport }) {
  const [state, setState] = useState(null);
  const [profile, setProfile] = useState(null);
  const [step, setStep] = useState('identity');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const [savedNote, setSavedNote] = useState(null);

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
    try { setState(await api.onboardingStep(next)); } catch {}
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

  const done = useMemo(() => ({
    identity: Boolean(state?.identity?.ready),
    mailbox: Boolean(state?.mailbox?.connected),
    import: (state?.import?.transactions || 0) > 0,
  }), [state]);

  if (!state) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {error ? <Callout tone="warn">{error}</Callout> : <Loading message="Loading setup..." />}
      </div>
    );
  }

  return (
    <div style={{
      minHeight: '100vh', width: '100vw', display: 'flex',
      alignItems: 'center', justifyContent: 'center', padding: '24px',
      position: 'relative',
    }}>
      <div style={{ position: 'absolute', top: 20, right: 20 }}>
        <IconButton
          icon={theme === 'dark' ? 'sun' : 'moon'}
          label="Toggle theme"
          onClick={toggleTheme}
        />
      </div>

      <div className="glass-card" style={{ maxWidth: 680, width: '100%', padding: '36px 40px' }}>
        <div className="flex items-start justify-between" style={{ marginBottom: 16 }}>
          <div>
            <div className="flex items-center gap-2" style={{ marginBottom: 6 }}>
              <Logo size={20} />
              <span style={{ fontWeight: 800, fontSize: 16 }}>PRISM v3</span>
            </div>
            <h1 style={{ fontSize: 24, fontWeight: 800 }}>
              Welcome{state.user?.name ? `, ${state.user.name.split(' ')[0]}` : ''}
            </h1>
            <p style={{ color: 'var(--text-2)', fontSize: 13.5, marginTop: 2 }}>
              Three quick steps to configure automatic statement ingestion.
            </p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => finish(false)} disabled={busy}>
            Skip setup
          </Button>
        </div>

        <ProgressBar value={((index + 1) / STEPS.length) * 100} tall tone="accent" />

        <div className="flex items-center gap-4" style={{ margin: '18px 0 24px' }}>
          {STEPS.map(([key, label, why], i) => (
            <button
              key={key}
              type="button"
              onClick={() => goTo(key)}
              title={why}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 8,
                background: 'none', border: 'none', cursor: 'pointer',
                color: i === index ? 'var(--accent)' : done[key] ? 'var(--pos)' : 'var(--text-3)',
                fontWeight: i === index ? 700 : 500, fontSize: 13,
              }}
            >
              <span style={{
                width: 22, height: 22, borderRadius: '50%',
                background: i === index ? 'var(--accent-soft)' : done[key] ? 'var(--pos-soft)' : 'var(--surface-3)',
                color: i === index ? 'var(--accent)' : done[key] ? 'var(--pos)' : 'var(--text-3)',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 11,
              }}>
                {done[key] ? '✓' : i + 1}
              </span>
              <span>{label}</span>
            </button>
          ))}
        </div>

        {error && <Callout tone="warn">{error}</Callout>}

        <div key={step} className="animate-fade-in">
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
            <ImportStep
              state={state}
              busy={busy}
              onImport={() => finish(true)}
              onLater={() => finish(false)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

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
    <form className="flex-col gap-3" onSubmit={saveAndContinue}>
      <h2 style={{ fontSize: 18, fontWeight: 700 }}>Personal Decryption Credentials</h2>
      <p style={{ color: 'var(--text-2)', fontSize: 13.5 }}>
        Bank and card statements are encrypted with passwords derived from your name, date of birth, mobile or PAN.
        Saving these allows protected PDFs to open automatically without prompting.
      </p>

      <Callout tone="acc">
        Credentials are encrypted and stored locally against your user account. Never shared with any LLM.
      </Callout>

      <Field label="Full name, as printed on your statements">
        <input
          type="text" value={form.full_name} onChange={set('full_name')}
          placeholder="e.g. Rahul Sharma" ref={first}
          style={{ width: '100%', padding: '8px 12px', borderRadius: 'var(--r-sm)', border: '1px solid var(--line-strong)', background: 'var(--surface)', color: 'var(--text)' }}
        />
      </Field>
      <div className="grid-2">
        <Field label="Date of birth">
          <input
            type="date" value={form.date_of_birth} onChange={set('date_of_birth')}
            style={{ width: '100%', padding: '8px 12px', borderRadius: 'var(--r-sm)', border: '1px solid var(--line-strong)', background: 'var(--surface)', color: 'var(--text)' }}
          />
        </Field>
        <Field
          label="Mobile number (10 digits)"
          hint={mobileOdd ? 'Standard Indian mobile numbers are 10 digits' : undefined}
        >
          <input
            type="text" inputMode="numeric" value={form.mobile} onChange={set('mobile')}
            placeholder="9876543210"
            style={{ width: '100%', padding: '8px 12px', borderRadius: 'var(--r-sm)', border: '1px solid var(--line-strong)', background: 'var(--surface)', color: 'var(--text)' }}
          />
        </Field>
      </div>
      <Field
        label="PAN (Permanent Account Number)"
        hint={panOdd ? 'Format: 5 letters, 4 digits, 1 letter' : undefined}
      >
        <input
          type="text" value={form.pan} onChange={set('pan')}
          placeholder="ABCDE1234F" style={{ textTransform: 'uppercase', width: '100%', padding: '8px 12px', borderRadius: 'var(--r-sm)', border: '1px solid var(--line-strong)', background: 'var(--surface)', color: 'var(--text)' }}
        />
      </Field>

      {problem && <Callout tone="warn">{problem}</Callout>}

      <div className="flex items-center gap-3" style={{ marginTop: 12 }}>
        <Button variant="primary" type="submit" busy={status === 'saving'}>
          Save and continue
        </Button>
        <Button type="button" variant="ghost" onClick={onNext}>
          {anything ? 'Continue without saving' : 'Skip for now'}
        </Button>
      </div>
    </form>
  );
}

function MailboxStep({ state, savedNote, onNext }) {
  const { connected, available } = state.mailbox;

  return (
    <div className="flex-col gap-3">
      {savedNote?.password_candidates > 0 && (
        <Callout tone="pos">
          <strong>Credentials saved.</strong> Generated {savedNote.password_candidates} candidate passwords to unlock PDFs automatically.
        </Callout>
      )}

      <h2 style={{ fontSize: 18, fontWeight: 700 }}>Auto-Sync from Gmail</h2>
      <p style={{ color: 'var(--text-2)', fontSize: 13.5 }}>
        Instead of downloading statements manually, Prism can auto-scan your Gmail
        for bank and credit card statement emails.
      </p>

      {connected ? (
        <Callout tone="pos">
          <strong>Gmail is connected.</strong> You are ready to scan statements.
        </Callout>
      ) : available ? (
        <div className="flex-col gap-3">
          <ul style={{ paddingLeft: 18, fontSize: 13, color: 'var(--text-2)', lineHeight: 1.6 }}>
            <li><strong>Read-only permission:</strong> Prism can only read statement attachments, never send or delete emails.</li>
            <li><strong>Selective import:</strong> You review all scanned files before anything is imported.</li>
          </ul>
          <div className="flex items-center gap-3">
            <Button variant="primary" icon="mail" onClick={() => api.gmailConnect()}>
              Connect Gmail
            </Button>
            <Button variant="ghost" onClick={onNext}>I’ll upload files manually</Button>
          </div>
        </div>
      ) : (
        <Callout tone="warn">
          Gmail auto-sync is not configured on this server. You can upload statements directly from your computer.
        </Callout>
      )}

      {(connected || !available) && (
        <div style={{ marginTop: 12 }}>
          <Button variant="primary" onClick={onNext}>Continue</Button>
        </div>
      )}
    </div>
  );
}

function ImportStep({ state, busy, onImport, onLater }) {
  const already = state.import.transactions > 0;
  return (
    <div className="flex-col gap-3">
      <h2 style={{ fontSize: 18, fontWeight: 700 }}>First Statement Import</h2>
      <p style={{ color: 'var(--text-2)', fontSize: 13.5 }}>
        Upload your bank, card, or loan statements. The balance reconciliation gate will verify
        opening and closing figures before adding rows to your ledger.
      </p>

      {already ? (
        <Callout tone="pos">
          <strong>{count(state.import.transactions)} transactions are already reconciled in your ledger.</strong>
        </Callout>
      ) : (
        <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.6 }}>
          All statements are checked against:
          <ul style={{ paddingLeft: 18, marginTop: 4 }}>
            <li><strong>Balance Gate:</strong> Opening + credits − debits must tie to printed closing balance.</li>
            <li><strong>Transfer Detection:</strong> Card bill payments and SIPs are matched across accounts.</li>
          </ul>
        </div>
      )}

      <div className="flex items-center gap-3" style={{ marginTop: 14 }}>
        <Button variant="primary" icon="upload" onClick={onImport} busy={busy}>
          Import statements
        </Button>
        <Button variant="ghost" onClick={onLater} disabled={busy}>
          {already ? 'Go to dashboard' : 'I’ll do this later'}
        </Button>
      </div>
    </div>
  );
}
