import React, { useEffect, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import {
  Button, Callout, Chip, ConfirmButton, GlassCard, Field, Loading, Badge,
} from '../ui';
import { Icon } from '../ui/icons';

export default function Profile() {
  const toast = useToast();
  const { data, loading, refetch } = useQuery('profile', () => api.profile());
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [candidates, setCandidates] = useState(null);
  const [error, setError] = useState(null);

  /* Passwords are their own list, managed one at a time. They are deliberately
     never sent to the browser, so the screen holds only a length per entry -
     enough to tell them apart and delete one, and nothing that discloses a
     password. */
  const [newPassword, setNewPassword] = useState('');
  const [pwBusy, setPwBusy] = useState(null);
  const [hints, setHints] = useState([]);

  useEffect(() => {
    if (!data) return;
    setHints(data.custom_password_hints || []);
    if (form) return;
    setForm({
      full_name: data.full_name || '',
      date_of_birth: data.date_of_birth || '',
      pan: data.pan || '',
      mobile: data.mobile || '',
    });
  }, [data, form]);

  if (loading || !form) return <Loading message="Reading identity parameters…" />;

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  /* Identity only. `custom_passwords` is deliberately not sent: the server
     reads an absent list as "leave them alone", so correcting your own name
     can no longer take the stored passwords with it. */
  async function save() {
    setSaving(true);
    setError(null);
    try {
      const res = await api.saveProfile(form);
      setCandidates(res.password_candidates);
      refetch();
      toast.ok(
        'Credentials saved',
        res.password_candidates > 0
          ? `${res.password_candidates} candidate password permutations generated for statement unlocking.`
          : 'Saved successfully.',
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function addPassword() {
    const value = newPassword.trim();
    if (!value) return;
    setPwBusy('add');
    try {
      const res = await api.addProfilePassword(value);
      setHints(res.custom_password_hints || []);
      setCandidates(res.password_candidates);
      setNewPassword('');
      refetch();
      toast.ok(
        res.status === 'already_stored' ? 'Already stored' : 'Password added',
        res.status === 'already_stored'
          ? 'That password was already on the list — nothing changed.'
          : `${res.custom_password_count} known password${res.custom_password_count === 1 ? '' : 's'} now stored.`,
      );
    } catch (e) {
      toast.fail('Could not add password', e.message);
    } finally {
      setPwBusy(null);
    }
  }

  async function removePassword(index) {
    setPwBusy(`del-${index}`);
    try {
      const res = await api.deleteProfilePassword(index);
      setHints(res.custom_password_hints || []);
      setCandidates(res.password_candidates);
      refetch();
      toast.ok('Password removed', `${res.custom_password_count} left.`);
    } catch (e) {
      toast.fail('Could not remove password', e.message);
    } finally {
      setPwBusy(null);
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

      {/* Identity */}
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

      {/* Known passwords — a list, not a text field, so adding one cannot
          replace the rest. */}
      <GlassCard style={{ padding: 'var(--space-6)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
          <div>
            <h3 className="h3" style={{ margin: 0 }}>Known PDF Passwords</h3>
            <p className="small muted" style={{ margin: '4px 0 0 0' }}>
              For banks whose password is not derivable from your name, date of birth or PAN.
              Tried before the derived candidates.
            </p>
          </div>
          <Chip tone={hints.length ? 'pos' : ''} size="sm">
            {hints.length} stored
          </Chip>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 'var(--space-4)' }}>
          {hints.map((hint) => (
            <div
              key={hint.index}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                gap: 'var(--space-3)', flexWrap: 'wrap',
                padding: '8px 12px', borderRadius: 'var(--r-sm)',
                background: 'var(--surface-2)', border: '1px solid var(--line)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                <Icon name="key" size={15} style={{ flexShrink: 0, color: 'var(--text-3)' }} />
                <span className="small font-medium">Password {hint.index + 1}</span>
                <span className="tiny muted">{hint.length} characters</span>
              </div>
              <ConfirmButton
                size="xs"
                variant="danger"
                disabled={pwBusy === `del-${hint.index}`}
                question="Remove this password?"
                confirmLabel="Remove"
                onConfirm={() => removePassword(hint.index)}
              >
                Remove
              </ConfirmButton>
            </div>
          ))}

          {!hints.length && (
            <div className="tiny muted">
              None stored. Add one below if a statement will not open with your
              name, date of birth or PAN.
            </div>
          )}
        </div>

        <div style={{
          display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap',
          marginTop: 'var(--space-4)', paddingTop: 'var(--space-3)',
          borderTop: '1px solid var(--border-subtle)',
        }}>
          <input
            className="input"
            type="text"
            autoComplete="off"
            spellCheck={false}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addPassword(); }}
            placeholder="Add another known password"
            style={{ flex: '1 1 220px', minWidth: 0 }}
          />
          <Button
            variant="primary"
            icon="plus"
            busy={pwBusy === 'add'}
            disabled={!newPassword.trim()}
            onClick={addPassword}
          >
            Add Password
          </Button>
        </div>

        <div className="tiny muted" style={{ marginTop: 'var(--space-3)' }}>
          Stored passwords are never shown again — on most banks they encode your
          date of birth, so the app keeps them write-only. Adding one leaves the
          others alone.
        </div>
      </GlassCard>
    </div>
  );
}
