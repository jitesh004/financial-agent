/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Omni-Copilot Drawer (⌘J)
   Context-aware AI Financial Assistant with live reasoning trace and verified figures.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useEffect, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useCopilot } from '../core/copilot';
import { useRoute } from '../core/router';
import { usePeriod } from '../core/period';
import { Icon } from '../ui/icons';
import { Button, Callout, IconButton } from '../ui';
import JobProgress from '../ui/JobProgress';
import { AgentAnswerView } from '../ui/AgentAnswerView';

/* Backend icon name → the icon set this app ships. */
const ICONS = {
  scale: 'scales', drip: 'repeat', wave: 'gauge', receipt: 'file', shield: 'shield',
  alarm: 'clock', stairs: 'trending', gauge: 'target', magnifier: 'search',
  coin: 'wallet', pulse: 'briefcase', scales: 'database',
};

export default function CopilotDrawer() {
  const {
    open, closeCopilot, suggestedPrompts, runAgentJob,
    running, activeJob, currentRun,
  } = useCopilot();
  const { path } = useRoute();
  const { label: periodLabel } = usePeriod();

  /* The agent roster comes from the server: hard-coding keys here silently
     404s every run the moment the backend renames or adds one. */
  const { data: catalogue } = useQuery('agents', () => api.agents(), { enabled: open });
  const agents = catalogue?.agents || [];

  const [selectedAgent, setSelectedAgent] = useState('');
  const [customPrompt, setCustomPrompt] = useState('');

  useEffect(() => {
    if (!selectedAgent && agents.length) setSelectedAgent(agents[0].key);
  }, [agents, selectedAgent]);

  if (!open) return null;

  const handleRun = (promptText = customPrompt) => {
    if (!selectedAgent) return;
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
                  disabled={running || !selectedAgent}
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
              <div className="flex items-center justify-between gap-2 flex-wrap" style={{ marginBottom: 12 }}>
                <span className="badge badge-accent">
                  <Icon name="check-circle" size={13} /> Answer verified
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                  {currentRun.agent_name || currentRun.agent}
                  {currentRun.seconds != null ? ` · ${currentRun.seconds.toFixed(1)}s` : ''}
                </span>
              </div>

              {currentRun.error && <Callout tone="warn">{currentRun.error}</Callout>}

              {/* `answer` is a structured object, never a renderable string. */}
              <AgentAnswerView answer={currentRun.answer} compact />
            </div>
          )}

          {/* Select Agent Dropdown */}
          <div style={{ marginTop: 'auto' }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: 6 }}>
              Specialized Agent
            </div>
            <div className="grid-2" style={{ gap: 8 }}>
              {agents.map((ag) => (
                <button
                  key={ag.key}
                  type="button"
                  title={ag.blurb || ag.question}
                  onClick={() => setSelectedAgent(ag.key)}
                  style={{
                    padding: '8px 10px', borderRadius: 'var(--r-sm)',
                    border: `1px solid ${selectedAgent === ag.key ? 'var(--accent)' : 'var(--line)'}`,
                    background: selectedAgent === ag.key ? 'var(--accent-soft)' : 'var(--surface-2)',
                    color: selectedAgent === ag.key ? 'var(--accent)' : 'var(--text)',
                    fontSize: 12.5, fontWeight: 600, textAlign: 'left', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', gap: 6, minWidth: 0,
                  }}
                >
                  <Icon name={ICONS[ag.icon] || 'sparkles'} size={14} />
                  <span className="truncate">{ag.name}</span>
                </button>
              ))}
              {!agents.length && (
                <span className="tiny muted">Loading agent catalogue…</span>
              )}
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
              disabled={running || !customPrompt.trim() || !selectedAgent}
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
