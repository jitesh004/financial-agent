import React, { useEffect, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { Button, Callout, Card, GlassCard, Field, Loading, Badge } from '../ui';

export default function Profile() {
  const toast = useToast();
  const { data, loading, refetch } = useQuery('profile', () => api.profile());
  const [form, setForm] = useState(null);
  const [customText, setCustomText] = useState('');
  const [savedPasswordCount, setSavedPasswordCount] = useState(0);
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
    // Never prefilled: the API no longer returns the passwords themselves.
    // Left blank means "keep what is stored"; typing replaces them.
    setCustomText('');
    setSavedPasswordCount(data.custom_password_count || 0);
  }, [data, form]);

  if (loading || !form) return <Loading message="Reading identity parameters…" />;

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
      toast.ok(
        'Credentials saved',
        res.password_candidates > 0
          ? `${res.password_candidates} candidate password permutations generated for statement unlocking.`
          : 'Saved successfully.'
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="profile-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', maxWidth: 720 }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Statement Decryption Identity</h1>
            <Badge tone="brand" size="sm">Local PDF Unlocker</Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Parameters used strictly by local PDF parser routines to automatically unlock password-protected bank,
            credit card, and depository statements.
          </p>
        </div>
      </div>

      <Callout tone="info">
        These credentials are never transmitted to external APIs or language models.
        They remain encrypted in your database to unlock bank statements (e.g. HDFC, ICICI, SBI) that use
        combinations like your name and date of birth.
      </Callout>

      <GlassCard glowing style={{ padding: 'var(--space-6)' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Field label="Full Name (as printed on bank statements)">
            <input
              className="input"
              type="text"
              value={form.full_name}
              onChange={set('full_name')}
              placeholder="e.g. Rahul Sharma"
            />
          </Field>

          <div className="grid-2">
            <Field label="Date of Birth">
              <input
                className="input"
                type="date"
                value={form.date_of_birth}
                onChange={set('date_of_birth')}
              />
            </Field>

            <Field label="Mobile Number">
              <input
                className="input"
                type="text"
                inputMode="numeric"
                value={form.mobile}
                onChange={set('mobile')}
                placeholder="10-digit registered number"
              />
            </Field>
          </div>

          <Field label="Permanent Account Number (PAN) — for CAS & Demat statements">
            <input
              className="input"
              type="text"
              value={form.pan}
              onChange={set('pan')}
              placeholder="ABCDE1234F"
              style={{ textTransform: 'uppercase' }}
            />
          </Field>

          <Field
            label="Known PDF Passwords (Optional, comma-separated)"
            hint="For non-standard password schemes, specify known passwords here."
          >
            <input
              className="input"
              type="text"
              value={customText}
              onChange={(e) => setCustomText(e.target.value)}
              placeholder="secretPass123, cardLast4"
            />
          </Field>

          {error && <Callout tone="neg">{error}</Callout>}

          {candidates != null && !error && (
            <Callout tone="pos">
              {candidates > 0
                ? `${candidates} candidate permutations computed and ready to unlock protected statement files.`
                : 'Identity updated.'}
            </Callout>
          )}

          <div style={{ marginTop: 'var(--space-2)' }}>
            <Button variant="primary" busy={saving} onClick={save}>
              Save Decryption Credentials
            </Button>
          </div>
        </div>
      </GlassCard>
    </div>
  );
}
