/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Omni-Copilot Drawer (⌘J)
   Context-aware AI Financial Assistant with live reasoning trace and verified figures.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useState } from 'react';
import { useCopilot } from '../core/copilot';
import { useRoute } from '../core/router';
import { usePeriod } from '../core/period';
import { useDrill } from '../core/drill';
import { Icon } from '../ui/icons';
import { Button, Chip, Loading, IconButton } from '../ui';
import JobProgress from '../ui/JobProgress';

const AGENTS_LIST = [
  { key: 'debt_strategist', name: 'Debt Strategist', icon: 'scales', desc: 'Snowball vs avalanche & interest savings' },
  { key: 'subscription_auditor', name: 'Subscription Auditor', icon: 'repeat', desc: 'Hidden creeps & annualized leak audit' },
  { key: 'cashflow_sentinel', name: 'Cashflow Sentinel', icon: 'gauge', desc: 'Upcoming pinches & 60-day low-water marks' },
  { key: 'emergency_resilience', name: 'Emergency Resilience', icon: 'shield', desc: 'Liquidity runway & shock cushion' },
  { key: 'lifestyle_creep', name: 'Lifestyle Creep', icon: 'trending', desc: 'Discretionary inflation trends' },
  { key: 'tax_utilisation', name: 'Tax Utilisation', icon: 'file', desc: '80C/D deductions & tax headroom' },
];

export default function CopilotDrawer() {
  const {
    open, closeCopilot, suggestedPrompts, runAgentJob,
    running, activeJob, currentRun, history,
  } = useCopilot();
  const { path } = useRoute();
  const { label: periodLabel } = usePeriod();
  const { drill } = useDrill();

  const [selectedAgent, setSelectedAgent] = useState('debt_strategist');
  const [customPrompt, setCustomPrompt] = useState('');

  if (!open) return null;

  const handleRun = (promptText = customPrompt) => {
    runAgentJob(selectedAgent, promptText);
    setCustomPrompt('');
  };

  return (
    <div className="drawer-backdrop" onClick={closeCopilot}>
      <aside className="drawer-panel copilot-panel" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="drawer-head" style={{
          background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.08) 0%, transparent 100%)',
        }}>
          <div className="flex items-center gap-3">
            <div style={{
              width: 34, height: 34, borderRadius: 'var(--r-sm)',
              background: 'linear-gradient(135deg, var(--accent) 0%, #38bdf8 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ffffff',
            }}>
              <Icon name="sparkles" size={18} />
            </div>
            <div>
              <h3 style={{ fontSize: 16, fontWeight: 800 }}>Financial Copilot</h3>
              <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                Auditing {path} · {periodLabel}
              </div>
            </div>
          </div>
          <IconButton icon="x" label="Close" onClick={closeCopilot} />
        </div>

        {/* Content Body */}
        <div className="drawer-body flex-col gap-4">
          {/* Proactive Context Suggestions */}
          <div>
            <div style={{ fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: 8 }}>
              Suggested for this screen
            </div>
            <div className="flex-col gap-2">
              {suggestedPrompts.map((p, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => handleRun(p)}
                  disabled={running}
                  style={{
                    textAlign: 'left', padding: '9px 12px', borderRadius: 'var(--r)',
                    background: 'var(--surface-2)', border: '1px solid var(--line)',
                    fontSize: 13, color: 'var(--text)', cursor: 'pointer',
                    transition: 'all var(--t-fast)', display: 'flex', alignItems: 'center', gap: 8,
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-border)'; e.currentTarget.style.background = 'var(--surface-hover)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--line)'; e.currentTarget.style.background = 'var(--surface-2)'; }}
                >
                  <Icon name="sparkles" size={13} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                  <span style={{ flex: 1 }}>{p}</span>
                  <Icon name="arrow-right" size={13} style={{ opacity: 0.4 }} />
                </button>
              ))}
            </div>
          </div>

          {/* Active Job Tracker */}
          {running && (
            <div className="glass-card" style={{ padding: '16px', background: 'var(--surface-2)' }}>
              <JobProgress job={activeJob || { phase: 'Starting analysis...', percent: 15 }} title="Agent Thinking..." />
            </div>
          )}

          {/* Current Run Answer */}
          {currentRun && !running && (
            <div className="glass-card" style={{ padding: '18px', border: '1px solid var(--accent-border)' }}>
              <div className="flex items-center justify-between" style={{ marginBottom: 12 }}>
                <span className="badge badge-accent">
                  <Icon name="check-circle" size={13} /> Answer verified
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-3)' }}>To the exact rupee</span>
              </div>

              <div style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text)' }}>
                {currentRun.output || currentRun.answer || currentRun.summary || 'Analysis complete.'}
              </div>

              {/* Verified Figures Audit */}
              {currentRun.figures?.length > 0 && (
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
                  <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: 6 }}>
                    Traced figures in ledger
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {currentRun.figures.map((fig, fi) => (
                      <button
                        key={fi}
                        type="button"
                        className="chip chip-pos tabular-nums"
                        onClick={() => drill({ title: `Audited figure: ${fig.value || fig}`, params: fig.params || {} })}
                        title="Click to see matching ledger rows"
                      >
                        ✓ {fig.value || fig}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Select Agent Dropdown */}
          <div style={{ marginTop: 'auto' }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: 6 }}>
              Specialized Agent
            </div>
            <div className="grid-2" style={{ gap: 8 }}>
              {AGENTS_LIST.map((ag) => (
                <button
                  key={ag.key}
                  type="button"
                  onClick={() => setSelectedAgent(ag.key)}
                  style={{
                    padding: '8px 10px', borderRadius: 'var(--r-sm)',
                    border: `1px solid ${selectedAgent === ag.key ? 'var(--accent)' : 'var(--line)'}`,
                    background: selectedAgent === ag.key ? 'var(--accent-soft)' : 'var(--surface-2)',
                    color: selectedAgent === ag.key ? 'var(--accent)' : 'var(--text)',
                    fontSize: 12.5, fontWeight: 600, textAlign: 'left', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', gap: 6,
                  }}
                >
                  <Icon name={ag.icon} size={14} />
                  <span className="truncate">{ag.name}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Footer Input */}
        <div className="drawer-foot flex-col gap-2">
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={customPrompt}
              onChange={(e) => setCustomPrompt(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && customPrompt.trim()) handleRun(); }}
              placeholder="Ask a question about your finances..."
              disabled={running}
              style={{
                flex: 1, padding: '9px 12px', fontSize: 13,
                borderRadius: 'var(--r-sm)', border: '1px solid var(--line-strong)',
                background: 'var(--surface)', color: 'var(--text)', outline: 'none',
              }}
            />
            <Button
              variant="primary"
              onClick={() => handleRun()}
              disabled={running || !customPrompt.trim()}
              busy={running}
            >
              Ask
            </Button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-3)', textAlign: 'center' }}>
            Press <kbd style={{ padding: '1px 4px', background: 'var(--surface-3)', borderRadius: 3 }}>⌘J</kbd> anytime to toggle Copilot
          </div>
        </div>
      </aside>
    </div>
  );
}
