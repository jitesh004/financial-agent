/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Sign-In Screen
   ──────────────────────────────────────────────────────────────────────── */

import React from 'react';
import { useAuth } from '../core/auth';
import { useTheme } from '../core/theme';
import { Button, Callout, IconButton } from '../ui';
import { GoogleMark, Logo } from '../ui/icons';

const POINTS = [
  ['Arithmetic ties to the rupee.', 'No language model does math. Balances and totals are calculated with Decimal precision and checked by balance reconciliation gates.'],
  ['Transfer netting removes double-counting.', 'Credit card payments, loan EMIs, and SIP transfers are detected across accounts so spending is never inflated.'],
  ['Your data stays yours alone.', 'Every ledger row is isolated per user at the database layer. Deleting your account wipes all records permanently.'],
];

export default function SignIn() {
  const { config, signIn, authError, dismissAuthError } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const configured = config?.configured !== false;

  return (
    <div style={{
      minHeight: '100vh', width: '100vw', display: 'flex',
      alignItems: 'center', justifyContent: 'center', padding: '24px',
      position: 'relative', overflow: 'hidden',
    }}>
      <div style={{ position: 'absolute', top: 20, right: 20 }}>
        <IconButton
          icon={theme === 'dark' ? 'sun' : 'moon'}
          label="Toggle theme"
          onClick={toggleTheme}
        />
      </div>

      <div className="glass-card" style={{ maxWidth: 520, width: '100%', padding: '36px 40px' }}>
        <div className="flex items-center gap-3" style={{ marginBottom: 20 }}>
          <div style={{
            width: 38, height: 38, borderRadius: 'var(--r-sm)',
            background: 'linear-gradient(135deg, var(--accent) 0%, #38bdf8 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ffffff',
          }}>
            <Logo size={22} />
          </div>
          <div>
            <h2 style={{ fontSize: 22, fontWeight: 800 }}>PRISM <span style={{ color: 'var(--accent-text)' }}>v3</span></h2>
            <div style={{ fontSize: 12, color: 'var(--text-3)' }}>Intelligent Financial Operating System</div>
          </div>
        </div>

        <h1 style={{ fontSize: 24, fontWeight: 800, marginBottom: 10, letterSpacing: '-0.025em' }}>
          Reconciled financial intelligence
        </h1>
        <p style={{ color: 'var(--text-2)', fontSize: 14, lineHeight: 1.6, marginBottom: 24 }}>
          Upload bank, card, loan and investment statements. Get an auditable,
          reconciled picture of where your money actually goes — with AI Copilot analysis.
        </p>

        {authError && (
          <Callout tone="warn" style={{ marginBottom: 18 }}>
            {authError}
            <Button size="xs" variant="ghost" style={{ marginLeft: 8 }} onClick={dismissAuthError}>
              Dismiss
            </Button>
          </Callout>
        )}

        {configured ? (
          <div>
            <Button variant="primary" size="lg" style={{ width: '100%', gap: 12 }} onClick={signIn}>
              <GoogleMark /> Continue with Google
            </Button>
            <p style={{ fontSize: 12, color: 'var(--text-3)', textAlign: 'center', marginTop: 12 }}>
              Shares name and email. Mailbox access is an optional separate grant.
            </p>
          </div>
        ) : (
          <Callout tone="warn">
            <strong>Sign-in is not configured yet.</strong>
            <div style={{ marginTop: 4 }}>
              {config?.setup_hint || 'Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env'}
            </div>
          </Callout>
        )}

        <div style={{ marginTop: 32, paddingTop: 24, borderTop: '1px solid var(--line)', display: 'grid', gap: 14 }}>
          {POINTS.map(([title, body]) => (
            <div key={title} style={{ fontSize: 13, lineHeight: 1.6 }}>
              <strong style={{ color: 'var(--text)', display: 'block', marginBottom: 2 }}>{title}</strong>
              <span style={{ color: 'var(--text-2)' }}>{body}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
