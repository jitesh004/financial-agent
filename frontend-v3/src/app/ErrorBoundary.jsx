/* ────────────────────────────────────────────────────────────────────────────
   Screen-level error boundary.
   A fault in one screen must not blank the whole shell: the rail, topbar and
   every other route stay usable while this renders in the content area.
   ──────────────────────────────────────────────────────────────────────── */

import React from 'react';
import { Button, Icon } from '../ui';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Keep the stack in the console for whoever is debugging the screen.
    console.error('Screen crashed:', error, info?.componentStack);
  }

  componentDidUpdate(prev) {
    // A new route means a fresh attempt; clear the previous screen's error.
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div style={{ maxWidth: 620, margin: '6vh auto 0', textAlign: 'center' }}>
        <div style={{
          width: 48, height: 48, borderRadius: 'var(--r-lg)',
          background: 'var(--neg-soft)', border: '1px solid var(--neg-border)',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          color: 'var(--neg)', marginBottom: 14,
        }}>
          <Icon name="alert" size={24} />
        </div>
        <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 6 }}>
          This screen hit an error
        </h3>
        <p style={{ color: 'var(--text-2)', marginBottom: 8, fontSize: 13.5 }}>
          The rest of the app still works — use the sidebar to carry on, or retry this screen.
        </p>
        <pre style={{
          textAlign: 'left', fontSize: 11.5, fontFamily: 'var(--font-mono)',
          background: 'var(--surface-2)', border: '1px solid var(--line)',
          borderRadius: 'var(--r-sm)', padding: '10px 12px', margin: '0 0 16px',
          overflowX: 'auto', color: 'var(--text-2)',
        }}>
          {String(error?.message || error)}
        </pre>
        <Button variant="primary" onClick={() => this.setState({ error: null })}>
          Retry
        </Button>
      </div>
    );
  }
}
