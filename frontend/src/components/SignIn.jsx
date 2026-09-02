import React from 'react';
import { useAuth } from '../auth';
import { Callout } from './ui';

/* The door.
 *
 * Two things it has to do beyond offering a button. First, say what the app
 * will and will not be given: someone is about to point a tool at their entire
 * financial history, and the scope of the Google grant is the single most
 * reassuring fact available - identity only, with mailbox access asked for
 * separately and later, if at all.
 *
 * Second, fail legibly. An operator who has not configured an OAuth client
 * should see that here, not a Google page reading "invalid_client".
 */
export default function SignIn() {
  const { config, signIn, authError, dismissAuthError } = useAuth();
  const configured = config?.configured !== false;

  return (
    <div className="signin">
      <div className="signin-panel">
        <div className="signin-brand">
          <img src="/favicon.svg" alt="" width="26" height="26" />
          <span>Prism</span>
        </div>

        <h1>Understand where your money actually goes</h1>
        <p className="signin-lead">
          Read your bank, card, loan and investment statements. Every figure is
          reconciled against the balances your bank printed, and nothing counts
          until you have seen what was read.
        </p>

        {authError && (
          <Callout tone="warn" className="signin-error">
            {authError}
            <button className="btn" style={{ marginLeft: 10, padding: '2px 8px' }}
              onClick={dismissAuthError}>Dismiss</button>
          </Callout>
        )}

        {configured ? (
          <>
            <button className="btn primary signin-google" onClick={signIn}>
              <GoogleMark />
              Continue with Google
            </button>
            <p className="signin-scope">
              Signing in shares your name and email address, and nothing else.
              Reading your mailbox for statements is a separate permission you
              can grant later — or never.
            </p>
          </>
        ) : (
          <Callout tone="warn" style={{ textAlign: 'left' }}>
            <strong>Sign-in is not configured yet.</strong>
            <div style={{ marginTop: 6, lineHeight: 1.55 }}>
              {config?.setup_hint
                || 'Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on the server.'}
            </div>
          </Callout>
        )}

        <ul className="signin-points">
          <li>
            <strong>Your data is yours alone.</strong> Every row is stored
            against your account and the database itself refuses to serve one
            person’s statements to another.
          </li>
          <li>
            <strong>No model ever produces a figure.</strong> Arithmetic is
            exact and reconciles to the rupee; a language model only writes
            about numbers that are already final.
          </li>
          <li>
            <strong>Leave whenever you like.</strong> Deleting your account
            removes every statement, decision and dashboard with it.
          </li>
        </ul>
      </div>
    </div>
  );
}

/* Google's mark, inline. An external image would be a request to a third party
   made before anyone has agreed to anything. */
function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3C33.7 32.7 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 13 4 4 13 4 24s9 20 20 20 20-9 20-20c0-1.3-.1-2.6-.4-3.9z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.3 0-9.7-3.3-11.3-8l-6.5 5C9.6 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4.1 5.6l6.2 5.2C36.9 40.2 44 35 44 24c0-1.3-.1-2.6-.4-3.9z" />
    </svg>
  );
}
