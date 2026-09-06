/* The door.
 *
 * Two things it has to do beyond offering a button. First, say what the app
 * will and will not be given: somebody is about to point a tool at their
 * entire financial history, and the scope of the Google grant is the single
 * most reassuring fact available - identity only, with mailbox access asked
 * for separately and later, if at all.
 *
 * Second, fail legibly. An operator who has not configured an OAuth client
 * should see that here, not a Google page reading "invalid_client".
 */

import React from 'react';
import { useAuth } from '../core/auth';
import { useTheme } from '../core/theme';
import { Button, Callout, IconButton } from '../ui';
import { GoogleMark, Logo } from '../ui/icons';

const POINTS = [
  ['Your data is yours alone.',
    'Every row is stored against your account and the database itself refuses to '
    + 'serve one person’s statements to another.'],
  ['No model ever produces a figure.',
    'Arithmetic is exact and reconciles to the rupee; a language model only writes '
    + 'about numbers that are already final.'],
  ['Leave whenever you like.',
    'Deleting your account removes every statement, decision and dashboard with it.'],
];

export default function SignIn() {
  const { config, signIn, authError, dismissAuthError } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const configured = config?.configured !== false;

  return (
    <div className="gate">
      {/* Settable from the door. It used to be reachable only from the app's
          own header, which is behind this screen - so somebody on a dark
          desktop met a white page and had no way to say otherwise until they
          were already signed in. */}
      <div className="gate-corner">
        <IconButton
          icon={theme === 'dark' ? 'sun' : 'moon'}
          label="Toggle theme"
          className="ghost"
          onClick={toggleTheme}
        />
      </div>

      <div className="gate-panel">
        <div className="gate-brand"><Logo size={26} /> Prism</div>

        <h1 className="h1" style={{ fontSize: 25, marginBottom: 10 }}>
          Understand where your money actually goes
        </h1>
        <p className="lead" style={{ marginBottom: 22 }}>
          Read your bank, card, loan and investment statements. Every figure is
          reconciled against the balances your bank printed, and nothing counts until
          you have seen what was read.
        </p>

        {authError && (
          <Callout tone="warn" style={{ marginBottom: 16 }}>
            {authError}
            <Button size="xs" style={{ marginLeft: 10 }} onClick={dismissAuthError}>
              Dismiss
            </Button>
          </Callout>
        )}

        {configured ? (
          <>
            <Button variant="primary" size="lg" className="block" onClick={signIn}>
              <GoogleMark /> Continue with Google
            </Button>
            <p className="small dim" style={{ marginTop: 12, textAlign: 'center' }}>
              Signing in shares your name and email address, and nothing else. Reading
              your mailbox for statements is a separate permission you can grant later
              — or never.
            </p>
          </>
        ) : (
          <Callout tone="warn">
            <strong>Sign-in is not configured yet.</strong>
            <div style={{ marginTop: 6 }}>
              {config?.setup_hint
                || 'Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on the server.'}
            </div>
          </Callout>
        )}

        <ul style={{ marginTop: 26, display: 'grid', gap: 12 }}>
          {POINTS.map(([title, body]) => (
            <li key={title} className="small muted" style={{ lineHeight: 1.65 }}>
              <strong style={{ color: 'var(--text)' }}>{title}</strong> {body}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
