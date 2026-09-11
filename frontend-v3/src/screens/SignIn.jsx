/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Sign-In Screen

   Two panels: what the product is on the left, the one action on the right.
   On a narrow screen the left panel is hidden rather than stacked - a person
   signing in on a phone wants the button, and scrolling past three
   paragraphs of value proposition to reach it is worse than not seeing them.

   The left panel leads with a reconciliation tie-out rather than a stock
   illustration, because that IS the product: opening plus credits minus
   debits equals closing, checked on every statement, and a file that does
   not balance is refused rather than quietly averaged in. Saying it with
   the actual arithmetic is more honest than saying "bank-grade accuracy",
   and it is the thing that distinguishes this from every other importer.
   ──────────────────────────────────────────────────────────────────────── */

import React from 'react';
import { useAuth } from '../core/auth';
import { useTheme } from '../core/theme';
import { Button, Callout, IconButton } from '../ui';
import { GoogleMark, Icon, Logo } from '../ui/icons';

const POINTS = [
  ['shield', 'Arithmetic ties to the rupee',
    'No language model does math. Every total is Decimal arithmetic, checked '
    + 'against the statement’s own opening and closing balance.'],
  ['repeat', 'Transfer netting removes double-counting',
    'Card payments, EMIs and SIP debits are matched across accounts, so money '
    + 'moving between your own accounts never reads as spending.'],
  ['lock', 'Your data stays yours alone',
    'Every row is isolated per user in the database itself. Deleting your '
    + 'account wipes all of it, permanently.'],
];

/* The reconciliation gate, drawn. Real figures from a real statement:
   these are the shape a bank statement actually declares, and the point is
   that the app checks the bottom line rather than assuming it. */
const TIE_OUT = [
  ['Opening balance', '₹ 1,15,242.00', 'muted'],
  ['Credits', '+ ₹ 1,67,489.00', 'pos'],
  ['Debits', '− ₹ 1,66,974.00', 'neg'],
];

export default function SignIn() {
  const { config, signIn, authError, dismissAuthError } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const configured = config?.configured !== false;

  return (
    <div
      style={{
        minHeight: '100vh',
        width: '100%',
        display: 'grid',
        gridTemplateColumns: 'minmax(0, 1.05fr) minmax(0, 1fr)',
        background: 'var(--bg)',
        backgroundImage: 'var(--bg-mesh)',
        position: 'relative',
      }}
      className="signin-grid"
    >
      {/* Responsive without a stylesheet round-trip: one rule, scoped here,
          collapsing to a single column before the two panels get too narrow
          to read. */}
      <style>{`
        @media (max-width: 900px) {
          .signin-grid { grid-template-columns: 1fr !important; }
          .signin-aside { display: none !important; }
        }
      `}</style>

      <div style={{ position: 'absolute', top: 20, right: 20, zIndex: 2 }}>
        <IconButton
          icon={theme === 'dark' ? 'sun' : 'moon'}
          label="Toggle theme"
          onClick={toggleTheme}
        />
      </div>

      {/* ─────────────────────────────────────────────── left: what this is */}
      <aside
        className="signin-aside"
        style={{
          display: 'flex', flexDirection: 'column', justifyContent: 'center',
          gap: 'var(--space-8)', padding: '56px 6vw',
          borderRight: '1px solid var(--line)',
          background: 'var(--surface-2)',
        }}
      >
        <div className="flex items-center gap-3">
          <div style={{
            width: 40, height: 40, borderRadius: 'var(--r-sm)',
            background: 'linear-gradient(135deg, var(--accent) 0%, #38bdf8 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#ffffff', flexShrink: 0,
          }}>
            <Logo size={23} />
          </div>
          <div>
            <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: '-0.02em' }}>
              PRISM <span style={{ color: 'var(--accent-text)' }}>v3</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
              Intelligent Financial Operating System
            </div>
          </div>
        </div>

        <div>
          <h1 style={{
            fontSize: 34, fontWeight: 800, lineHeight: 1.15,
            letterSpacing: '-0.03em', margin: 0,
          }}>
            Every figure traced back
            <br />
            to the statement it came from.
          </h1>
          <p style={{
            color: 'var(--text-2)', fontSize: 15, lineHeight: 1.65,
            marginTop: 'var(--space-4)', maxWidth: '46ch',
          }}>
            Import bank, card, loan and investment statements. Prism reconciles
            them against each other and shows its working &mdash; so a number
            you do not believe can always be opened up.
          </p>
        </div>

        {/* The gate itself. */}
        <div
          className="glass-card"
          style={{ padding: '18px 20px', maxWidth: 380 }}
          aria-hidden="true"
        >
          <div className="flex items-center gap-2" style={{ marginBottom: 12 }}>
            <Icon name="check-circle" size={15} style={{ color: 'var(--pos)' }} />
            <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: '.02em' }}>
              Reconciliation gate
            </span>
            <span style={{ flex: 1 }} />
            <span className="tiny" style={{ color: 'var(--text-3)' }}>
              every statement
            </span>
          </div>

          {TIE_OUT.map(([label, value, tone]) => (
            <div
              key={label}
              className="flex items-center justify-between"
              style={{ fontSize: 12.5, padding: '3px 0' }}
            >
              <span style={{ color: 'var(--text-3)' }}>{label}</span>
              <span
                className="num"
                style={{
                  color: tone === 'pos' ? 'var(--pos)'
                    : tone === 'neg' ? 'var(--neg)' : 'var(--text-2)',
                }}
              >
                {value}
              </span>
            </div>
          ))}

          <div
            className="flex items-center justify-between"
            style={{
              fontSize: 13, fontWeight: 700, paddingTop: 8, marginTop: 6,
              borderTop: '1px solid var(--line)',
            }}
          >
            <span>Closing balance</span>
            <span className="num" style={{ color: 'var(--pos)' }}>
              &#8377; 1,15,757.00
            </span>
          </div>
          <div className="tiny" style={{ color: 'var(--text-3)', marginTop: 8 }}>
            Ties to the rupee. A file that does not balance is held back, not
            averaged in.
          </div>
        </div>

        <div style={{ display: 'grid', gap: 'var(--space-4)', maxWidth: '52ch' }}>
          {POINTS.map(([icon, title, body]) => (
            <div key={title} className="flex gap-3" style={{ alignItems: 'flex-start' }}>
              <Icon
                name={icon}
                size={16}
                style={{ color: 'var(--accent-text)', flexShrink: 0, marginTop: 3 }}
              />
              <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                <strong style={{ display: 'block', marginBottom: 1 }}>{title}</strong>
                <span style={{ color: 'var(--text-2)' }}>{body}</span>
              </div>
            </div>
          ))}
        </div>
      </aside>

      {/* ──────────────────────────────────────────── right: the one action */}
      <main style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '48px 24px',
      }}>
        <div style={{ width: '100%', maxWidth: 380 }}>
          {/* Shown only where the left panel is not - on a phone the product
              still has to introduce itself. */}
          <div
            className="flex items-center gap-3 signin-compact-brand"
            style={{ marginBottom: 'var(--space-6)' }}
          >
            <div style={{
              width: 36, height: 36, borderRadius: 'var(--r-sm)',
              background: 'linear-gradient(135deg, var(--accent) 0%, #38bdf8 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: '#ffffff', flexShrink: 0,
            }}>
              <Logo size={21} />
            </div>
            <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: '-0.02em' }}>
              PRISM <span style={{ color: 'var(--accent-text)' }}>v3</span>
            </div>
          </div>
          <style>{`
            .signin-compact-brand { display: none; }
            @media (max-width: 900px) { .signin-compact-brand { display: flex !important; } }
          `}</style>

          <h2 style={{
            fontSize: 22, fontWeight: 800, letterSpacing: '-0.02em',
            marginBottom: 6,
          }}>
            Sign in
          </h2>
          <p style={{ color: 'var(--text-2)', fontSize: 13.5, lineHeight: 1.6, marginBottom: 'var(--space-6)' }}>
            Your ledger is private to your account and stays on your own
            infrastructure.
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
            <>
              <Button
                variant="primary"
                size="lg"
                style={{ width: '100%', gap: 12 }}
                onClick={signIn}
              >
                <GoogleMark /> Continue with Google
              </Button>

              {/* What consenting actually grants, before the consent screen
                  rather than after it. Mailbox access is a SEPARATE grant
                  asked for later and only if the holder wants statements
                  fetched automatically - worth saying here, because "sign in
                  with Google" on a finance app otherwise reads as handing
                  over the mailbox. */}
              <ul style={{
                listStyle: 'none', margin: '16px 0 0', padding: 0,
                display: 'grid', gap: 8,
              }}>
                {[
                  ['check', 'Shares your name and email address'],
                  ['lock', 'Does not read your mailbox — that is a separate '
                    + 'grant you can decline, or revoke later'],
                ].map(([icon, text]) => (
                  <li key={text} className="flex gap-2" style={{ alignItems: 'flex-start' }}>
                    <Icon
                      name={icon}
                      size={13}
                      style={{ color: 'var(--text-3)', flexShrink: 0, marginTop: 3 }}
                    />
                    <span style={{ fontSize: 12, color: 'var(--text-3)', lineHeight: 1.55 }}>
                      {text}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <Callout tone="warn">
              <strong>Sign-in is not configured yet.</strong>
              <div style={{ marginTop: 4 }}>
                {config?.setup_hint
                  || 'Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env'}
              </div>
            </Callout>
          )}
        </div>
      </main>
    </div>
  );
}
