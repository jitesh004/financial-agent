import React, { useEffect, useState } from 'react';
import { api } from '../lib';
import { Callout, Card } from './ui';

/* The profile is what unlocks password-protected statements. It is deliberately
   framed as "your details, so we can open your own files" - the sensitivity of
   PAN and DOB is acknowledged rather than glossed over. */
export default function Profile({ onSaved }) {
  const [form, setForm] = useState({
    full_name: '', date_of_birth: '', pan: '', mobile: '', custom_passwords: [],
  });
  const [customText, setCustomText] = useState('');
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [candidates, setCandidates] = useState(null);

  useEffect(() => {
    api.profile().then((p) => {
      setForm({
        full_name: p.full_name || '',
        date_of_birth: p.date_of_birth || '',
        pan: p.pan || '',
        mobile: p.mobile || '',
        custom_passwords: p.custom_passwords || [],
      });
      setCustomText((p.custom_passwords || []).join(', '));
    }).catch(() => {});
  }, []);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function save() {
    setStatus('saving'); setError(null);
    try {
      const payload = {
        ...form,
        custom_passwords: customText.split(',').map((s) => s.trim()).filter(Boolean),
      };
      const res = await api.saveProfile(payload);
      setCandidates(res.password_candidates);
      setStatus('saved');
      onSaved?.();
    } catch (e) {
      setError(e.message); setStatus(null);
    }
  }

  return (
    <div style={{ maxWidth: 620, margin: '0 auto' }}>
      <Card title="Your details">
        <p style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 0 }}>
          Used to open your own password-protected statements automatically, and
          to match statements to the right account. Banks build statement
          passwords from these details — e.g. the first four letters of your name
          plus your date of birth, like <code>jite0602</code>.
        </p>

        <Callout>
          These details stay on this machine. They are stored in the local
          database, used only to open files you upload, and are never sent to any
          AI model or external service.
        </Callout>

        <div className="grid" style={{ gap: 12, marginTop: 14 }}>
          <Field label="Full name (as printed on statements)">
            <input type="text" value={form.full_name} onChange={set('full_name')}
              placeholder="e.g. John Adams" />
          </Field>

          <div className="grid cols-2" style={{ gap: 12 }}>
            <Field label="Date of birth">
              <input type="date" value={form.date_of_birth} onChange={set('date_of_birth')} />
            </Field>
            <Field label="Mobile number">
              <input type="text" inputMode="numeric" value={form.mobile}
                onChange={set('mobile')} placeholder="10 digits" />
            </Field>
          </div>

          <Field label="PAN (for mutual-fund / demat statements)">
            <input type="text" value={form.pan} onChange={set('pan')}
              placeholder="ABCDE1234F" style={{ textTransform: 'uppercase' }} />
          </Field>

          <Field label="Known passwords (optional, comma-separated)"
            hint="If a statement uses a format we don't guess, add its password here — tried first.">
            <input type="text" value={customText} onChange={(e) => setCustomText(e.target.value)}
              placeholder="mypass123, another-one" />
          </Field>
        </div>

        {error && <Callout tone="neg" style={{ marginTop: 12 }}>{error}</Callout>}
        {status === 'saved' && (
          <Callout tone="pos" style={{ marginTop: 12 }}>
            Saved. {candidates > 0
              ? `${candidates} candidate passwords will be tried against protected PDFs.`
              : 'Add a name and date of birth to generate password candidates.'}
          </Callout>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
          <button className="btn primary" onClick={save} disabled={status === 'saving'}>
            {status === 'saving' ? 'Saving…' : 'Save details'}
          </button>
          {onSaved && <button className="btn" onClick={onSaved}>Done</button>}
        </div>
      </Card>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block' }}>
      <div style={{ fontSize: 12.5, fontWeight: 550, marginBottom: 5 }}>{label}</div>
      {React.cloneElement(children, { style: { ...children.props.style, width: '100%' } })}
      {hint && <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>{hint}</div>}
    </label>
  );
}
