/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 AI Financial Intelligence Hub (Agents)
   Specialized agents auditing leaks, debt, resilience, taxes & cashflow shocks.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useState } from 'react';
import { api, watchJob } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { useDrill } from '../core/drill';
import { ago, dateLabel, duration, money, plural } from '../core/format';
import { Button, Callout, Card, GlassCard, Chip, ConfirmButton, Empty, Icon, Loading, Table } from '../ui';
import JobProgress from '../ui/JobProgress';

const ICONS = {
  scale: 'scales',
  drip: 'repeat',
  wave: 'gauge',
  receipt: 'file',
  shield: 'shield',
  alarm: 'clock',
  stairs: 'trending',
  gauge: 'target',
  magnifier: 'search',
  coin: 'wallet',
  pulse: 'briefcase',
  scales: 'database',
};

export default function Agents() {
  const toast = useToast();
  const { data: catalogue, loading, error, refetch } = useQuery('agents', () => api.agents());
  const [runningKey, setRunningKey] = useState(null);
  const [job, setJob] = useState(null);
  const [openRun, setOpenRun] = useState(null);
  const [historyFor, setHistoryFor] = useState(null);
  const [question, setQuestion] = useState({});

  async function open(runId) {
    setHistoryFor(null);
    try {
      setOpenRun(await api.agentRun(runId, { transcript: true }));
    } catch (e) {
      toast.fail('Could not open run', e.message);
    }
  }

  async function run(agent) {
    setOpenRun(null);
    setHistoryFor(null);
    setRunningKey(agent.key);
    setJob(null);
    try {
      const { job_id: jobId } = await api.runAgent(agent.key, question[agent.key] || '');
      const finished = await watchJob(jobId, setJob);
      if (finished.status === 'failed') {
        throw new Error(finished.errors?.join('; ') || 'The agent run failed');
      }
      if (finished.result?.run_id) await open(finished.result.run_id);
      refetch();
    } catch (e) {
      toast.fail('Agent run failed', e.message);
    } finally {
      setRunningKey(null);
    }
  }

  if (loading) return <Loading message="Reading specialized agent catalogue..." />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  const agents = catalogue?.agents || [];
  const blocked = catalogue && !catalogue.model_available;

  if (openRun) {
    return <AgentAnswer run={openRun} onBack={() => setOpenRun(null)} />;
  }

  const historyAgent = agents.find((a) => a.key === historyFor);

  return (
    <div className="flex-col gap-6 animate-fade-in">
      <div>
        <h1 style={{ fontSize: 24, fontWeight: 800 }}>AI Financial Agent Hub</h1>
        <p style={{ color: 'var(--text-2)', fontSize: 14, marginTop: 4, maxWidth: 760 }}>
          Specialized LangGraph models explore your ledger, invoke read-only financial tools,
          and produce auditable findings where every figure is guaranteed to tie out to the rupee.
        </p>
      </div>

      {blocked && (
        <Callout tone="warn">
          {catalogue.model_note || 'Model provider not configured. Add an API key in Settings to run AI agents.'}
        </Callout>
      )}

      {runningKey && job && (
        <GlassCard pad title={agents.find((a) => a.key === runningKey)?.name || 'Agent Execution'}>
          <JobProgress job={job} title={job.phase || 'Thinking...'} trace={false} />
        </GlassCard>
      )}

      {historyAgent ? (
        <History agent={historyAgent} onOpen={open} onClose={() => setHistoryFor(null)} onDeleted={refetch} />
      ) : (
        <div className="grid-2 gap-4">
          {agents.map((agent) => (
            <AgentCard
              key={agent.key}
              agent={agent}
              disabled={blocked}
              running={runningKey === agent.key}
              question={question[agent.key] || ''}
              onQuestion={(v) => setQuestion((p) => ({ ...p, [agent.key]: v }))}
              onRun={() => run(agent)}
              onOpen={() => open(agent.last_run.id)}
              onHistory={() => setHistoryFor(agent.key)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AgentCard({ agent, disabled, running, question, onQuestion, onRun, onOpen, onHistory }) {
  const icon = ICONS[agent.icon] || 'sparkles';
  const hasLastRun = agent.last_run?.id;

  return (
    <div className="card flex-col justify-between" style={{ padding: 22, height: '100%' }}>
      <div>
        <div className="flex items-start justify-between" style={{ marginBottom: 12 }}>
          <div className="flex items-center gap-2.5">
            <div style={{
              width: 36, height: 36, borderRadius: 'var(--r-sm)',
              background: 'var(--accent-soft)', color: 'var(--accent)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Icon name={icon} size={18} />
            </div>
            <div>
              <h3 style={{ fontSize: 16, fontWeight: 700 }}>{agent.name}</h3>
              <span className="text-3 text-xs">{agent.category || 'Specialized Agent'}</span>
            </div>
          </div>
          {agent.runs_count > 0 && (
            <button
              type="button"
              className="badge badge-accent"
              onClick={onHistory}
              title="View past executions"
              style={{ cursor: 'pointer', border: 'none' }}
            >
              {plural(agent.runs_count, 'run')}
            </button>
          )}
        </div>

        <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.5, marginBottom: 14 }}>
          {agent.description}
        </p>

        {agent.recommended_prompts?.length > 0 && (
          <div className="flex-col gap-1.5" style={{ marginBottom: 14 }}>
            {agent.recommended_prompts.slice(0, 2).map((rp, i) => (
              <button
                key={i}
                type="button"
                className="text-xs text-3 flex items-center gap-1.5"
                onClick={() => onQuestion(rp)}
                style={{ textAlign: 'left', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent)' }}
              >
                <span>✦ {rp}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex-col gap-2" style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
        <input
          type="text"
          value={question}
          onChange={(e) => onQuestion(e.target.value)}
          placeholder="Custom question (optional)..."
          disabled={disabled || running}
          style={{
            padding: '7px 10px', fontSize: 12.5, borderRadius: 'var(--r-sm)',
            border: '1px solid var(--line-strong)', background: 'var(--surface)', color: 'var(--text)', outline: 'none',
          }}
        />
        <div className="flex items-center justify-between">
          <Button
            variant="primary"
            size="sm"
            icon="play"
            disabled={disabled || running}
            busy={running}
            onClick={onRun}
          >
            Run Agent
          </Button>

          {hasLastRun && (
            <Button size="sm" variant="ghost" onClick={onOpen}>
              Latest Answer ({ago(agent.last_run.created_at)})
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function AgentAnswer({ run, onBack }) {
  const { drill } = useDrill();
  const [showTranscript, setShowTranscript] = useState(false);

  return (
    <div className="flex-col gap-4 animate-fade-in">
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" icon="arrow-left" onClick={onBack}>
          Back to Agents
        </Button>
        <span className="text-3 text-xs">Executed {dateLabel(run.created_at)} · {duration(run.elapsed_ms)}</span>
      </div>

      <GlassCard pad>
        <div className="flex items-center gap-2" style={{ marginBottom: 12 }}>
          <span className="badge badge-accent">
            <Icon name="check-circle" size={13} /> Reconciled Finding
          </span>
          <span className="text-3 text-xs">All arithmetic audited</span>
        </div>

        <h2 style={{ fontSize: 20, fontWeight: 800, marginBottom: 10 }}>{run.headline || run.agent_name || 'Agent Analysis'}</h2>

        {run.question && (
          <div style={{ padding: '8px 12px', background: 'var(--surface-2)', borderRadius: 'var(--r-sm)', fontSize: 13, marginBottom: 14 }}>
            <strong>Question:</strong> {run.question}
          </div>
        )}

        <div style={{ fontSize: 14, lineHeight: 1.65, color: 'var(--text)', whiteSpace: 'pre-wrap' }}>
          {run.output || run.answer || run.summary}
        </div>

        {/* Traced Figures */}
        {run.figures?.length > 0 && (
          <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--line)' }}>
            <div style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: 8 }}>
              Audited Figures (Click to view transactions in ledger)
            </div>
            <div className="flex flex-wrap gap-2">
              {run.figures.map((fig, i) => (
                <button
                  key={i}
                  type="button"
                  className="chip chip-pos tabular-nums"
                  onClick={() => drill({ title: `Audited figure: ${fig.value || fig}`, params: fig.params || {} })}
                  title="Drill down into matching rows"
                >
                  ✓ {fig.value || fig}
                </button>
              ))}
            </div>
          </div>
        )}
      </GlassCard>

      {/* Execution Transcript Accordion */}
      {run.transcript?.length > 0 && (
        <Card pad title="Tool Execution Transcript" sub="Exact read-only tool invocations and results">
          <Button size="xs" variant="secondary" onClick={() => setShowTranscript((v) => !v)}>
            {showTranscript ? 'Hide Step Details' : `Show ${run.transcript.length} Tool Steps`}
          </Button>

          {showTranscript && (
            <div className="flex-col gap-2" style={{ marginTop: 14 }}>
              {run.transcript.map((step, idx) => (
                <div key={idx} className="card" style={{ padding: '10px 14px', fontSize: 12.5, background: 'var(--surface-2)' }}>
                  <div className="flex items-center justify-between font-mono font-bold text-accent" style={{ marginBottom: 4 }}>
                    <span>Step {idx + 1}: {step.tool || step.action}</span>
                    <span className="text-3 text-xs">{duration(step.duration_ms)}</span>
                  </div>
                  {step.args && (
                    <div className="mono text-3 text-xs" style={{ marginBottom: 4 }}>
                      Args: {JSON.stringify(step.args)}
                    </div>
                  )}
                  {step.result && (
                    <div className="text-2 text-xs truncate">
                      Result: {typeof step.result === 'object' ? JSON.stringify(step.result) : String(step.result)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function History({ agent, onOpen, onClose, onDeleted }) {
  const { data: runs = [], loading } = useQuery(`agent-runs:${agent.key}`, () => api.agentRuns(agent.key));

  return (
    <Card
      pad
      title={`${agent.name} Execution History`}
      sub="Past model queries and verified answers"
      tools={<Button size="sm" variant="ghost" onClick={onClose}>Back to Catalogue</Button>}
    >
      {loading && <Loading message="Loading run history..." />}
      {!loading && runs.length === 0 && <Empty title="No runs recorded yet" icon="clock" />}
      {!loading && runs.length > 0 && (
        <div className="flex-col gap-3">
          {runs.map((r) => (
            <div
              key={r.id}
              className="card flex items-center justify-between"
              style={{ padding: '12px 16px', cursor: 'pointer' }}
              onClick={() => onOpen(r.id)}
            >
              <div>
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>{r.headline || r.question || 'Agent Run'}</div>
                <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                  {dateLabel(r.created_at)} · {duration(r.elapsed_ms)}
                </div>
              </div>
              <Button size="xs" variant="secondary">View Answer</Button>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
