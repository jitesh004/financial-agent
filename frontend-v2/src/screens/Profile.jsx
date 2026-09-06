/* The details that open your own password-protected statements.
 *
 * Framed as "your details, so we can open your own files" rather than as a
 * form: the sensitivity of a PAN and a date of birth is acknowledged on the
 * screen rather than glossed over, because being asked for a PAN out of
 * nowhere with no explanation is alarming in a way that being asked with the
 * reason attached is not.
 */

import React, { useEffect, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { Button, Callout, Card, Field, Loading } from '../ui';

export default function Profile() {
  const toast = useToast();
  const { data, loading, refetch } = useQuery('profile', () => api.profile());
  const [form, setForm] = useState(null);
  const [customText, setCustomText] = useState('');
  const [saving, setSaving] = useState(false);
  const [candidates, setCandidates] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!data || form) return;
    setForm({
      full_name: data.full_name || '',
      date_of_birth: data.date_of_birth || '',
      pan: data.pan || '',
      mobile: data.mobile || '',
    });
    setCustomText((data.custom_passwords || []).join(', '));
  }, [data, form]);

  if (loading || !form) return <Loading label="Reading your details…" />;

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const res = await api.saveProfile({
        ...form,
        custom_passwords: customText.split(',').map((s) => s.trim()).filter(Boolean),
      });
      setCandidates(res.password_candidates);
      refetch();
      toast.ok('Saved', res.password_candidates > 0
        ? `${res.password_candidates} candidate passwords will be tried against protected PDFs.`
        : 'Add a name and date of birth to generate password candidates.');
    } catch (e) {
      setError(e.message);
    } finally { setSaving(false); }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <Card title="Your details">
        <p className="lead" style={{ marginBottom: 12 }}>
          Used to open your own password-protected statements automatically, and to match
          statements to the right account. Indian banks build statement passwords from
          these details — the classic format is the first four letters of your name plus
          your date of birth, like <code>pank1407</code>.
        </p>

        <Callout tone="acc">
          These stay against your account. They are used only to open files you upload,
          and are never sent to any model or external service.
        </Callout>

        <div className="col" style={{ marginTop: 14 }}>
          <Field label="Full name, as printed on statements">
            <input type="text" value={form.full_name} onChange={set('full_name')}
              placeholder="e.g. John Adams" />
          </Field>

          <div className="grid cols-2">
            <Field label="Date of birth">
              <input type="date" value={form.date_of_birth} onChange={set('date_of_birth')} />
            </Field>
            <Field label="Mobile number">
              <input type="text" inputMode="numeric" value={form.mobile}
                onChange={set('mobile')} placeholder="10 digits" />
            </Field>
          </div>

          <Field label="PAN — for mutual fund and demat statements">
            <input type="text" value={form.pan} onChange={set('pan')}
              placeholder="ABCDE1234F" style={{ textTransform: 'uppercase' }} />
          </Field>

          <Field
            label="Known passwords (optional, comma-separated)"
            hint="If a statement uses a format the app does not guess, add its password here — it is tried first."
          >
            <input type="text" value={customText}
              onChange={(e) => setCustomText(e.target.value)}
              placeholder="mypass123, another-one" />
          </Field>
        </div>

        {error && <Callout tone="neg" style={{ marginTop: 12 }}>{error}</Callout>}
        {candidates != null && !error && (
          <Callout tone="pos" style={{ marginTop: 12 }}>
            Saved. {candidates > 0
              ? `${candidates} candidate password${candidates === 1 ? '' : 's'} will be tried `
                + 'against protected PDFs.'
              : 'Add a name and date of birth to generate password candidates.'}
          </Callout>
        )}

        <div className="row" style={{ marginTop: 14 }}>
          <Button variant="primary" busy={saving} onClick={save}>Save details</Button>
        </div>
      </Card>
    </div>
  );
}
