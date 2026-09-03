import React, { useState } from 'react';
import { switchDemo } from '../lib';

/* "You are looking at generated data."
 *
 * Loud, and above everything - including the empty-ledger screen, because a
 * demo workspace is empty for the moment between being created and being
 * seeded, and that is exactly when somebody most needs telling which data
 * they are looking at.
 *
 * The risk this exists to remove is not confusing a demo for real data during
 * a demo. It is coming back on Monday, forgetting the switch is on, and
 * concluding something about your own money from somebody else's numbers.
 */
export default function DemoBanner() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function turnOff() {
    setBusy(true);
    setError(null);
    try {
      // Reloads on success - see switchDemo.
      await switchDemo(false);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <div className="demo-banner" role="status">
      <span className="demo-badge">Demo</span>
      <span className="demo-text">
        Every figure on screen is <strong>generated</strong> — this is a demo
        workspace, not your ledger. Nothing you do here touches your own data.
        {error && <span className="demo-error"> {error}</span>}
      </span>
      <button className="btn" onClick={turnOff} disabled={busy}>
        {busy ? 'Switching…' : 'Show my real data'}
      </button>
    </div>
  );
}
