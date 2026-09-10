/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 AI Financial Intelligence Hub (Agents)
   Specialized agents auditing leaks, debt, resilience, taxes & cashflow shocks.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useState } from 'react';
import { Link } from '../core/router';
import { api, watchJob } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { ago, plural, stampLabel } from '../core/format';
import { Button, Callout, Card, GlassCard, Chip, ConfirmButton, Empty, Icon, Loading } from '../ui';
import JobProgress from '../ui/JobProgress';
import { AgentAnswerView, AgentRunDiff } from '../ui/AgentAnswerView';

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
  const budget = catalogue?.profile || {};
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
          {catalogue.model_note
            || 'No model provider is configured. Choose one and add an API key in Settings to run AI agents.'}
        </Callout>
      )}

      {budget.name && (
        <Callout tone="info">
          <strong>{budget.name === 'compact' ? 'Minimum' : 'Full'} step budget</strong>
          {budget.max_steps ? ` — up to ${plural(budget.max_steps, 'reasoning step')} per run.` : '.'}
          {budget.note ? ` ${budget.note}` : ''}
          {' '}
          <Link to="/settings">Change it in Settings</Link>.
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
              maxSteps={Math.min(agent.max_steps || 0, budget.max_steps || agent.max_steps || 0)}
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

function AgentCard({
  agent, disabled, running, question, maxSteps, onQuestion, onRun, onOpen, onHistory,
}) {
  const icon = ICONS[agent.icon] || 'sparkles';
  const hasLastRun = agent.last_run?.id;

  return (
    <div className="card flex-col justify-between" style={{ padding: 22, height: '100%' }}>
      <div>
        <div className="flex items-start justify-between" style={{ marginBottom: 12 }}>
          <div className="flex items-center gap-2.5">
            <div style={{
              width: 36, height: 36, borderRadius: 'var(--r-sm)',
              background: 'var(--accent-soft)', color: 'var(--accent-text)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Icon name={icon} size={18} />
            </div>
            <div style={{ minWidth: 0 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700 }}>{agent.name}</h3>
              <span className="text-3 text-xs">
                {agent.tools?.length ? plural(agent.tools.length, 'read-only tool') : 'Specialized agent'}
                {maxSteps ? ` · up to ${maxSteps} steps` : ''}
              </span>
            </div>
          </div>
          <button
            type="button"
            className="badge badge-accent"
            onClick={onHistory}
            title="View past executions"
            style={{ cursor: 'pointer', border: 'none' }}
          >
            History
          </button>
        </div>

        <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.5, marginBottom: 14 }}>
          {agent.blurb}
        </p>

        {agent.question && (
          <div className="flex-col gap-1.5" style={{ marginBottom: 14 }}>
            <button
              type="button"
              className="text-xs flex items-start gap-1.5"
              onClick={() => onQuestion(agent.question)}
              title="Use the agent's own default question"
              style={{ textAlign: 'left', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent-text)' }}
            >
              <span>&#10022; {agent.question}</span>
            </button>
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
            <Button
              size="sm"
              variant="ghost"
              onClick={onOpen}
              title={agent.last_run.headline || 'Open the most recent answer'}
            >
              Latest answer ({ago(agent.last_run.started_at)})
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function AgentAnswer({ run, onBack }) {
  const [showTranscript, setShowTranscript] = useState(false);
  const failed = run.status && run.status !== 'ok';

  return (
    <div className="flex-col gap-4 animate-fade-in">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <Button variant="ghost" size="sm" icon="arrow-left" onClick={onBack}>
          Back to Agents
        </Button>
        <span className="text-3 text-xs">
          {run.agent_name || run.agent} · {stampLabel(run.started_at)}
          {run.seconds != null ? ` · ${run.seconds.toFixed(1)}s` : ''}
          {run.steps != null ? ` · ${plural(run.steps, 'step')}` : ''}
          {run.tool_calls != null ? ` · ${plural(run.tool_calls, 'tool call')}` : ''}
        </span>
      </div>

      <GlassCard pad>
        <div className="flex items-center gap-2 flex-wrap" style={{ marginBottom: 12 }}>
          <span className={`badge ${failed ? 'badge-warn' : 'badge-accent'}`}>
            <Icon name={failed ? 'warning' : 'check-circle'} size={13} />
            {failed ? ' Completed with warnings' : ' Reconciled finding'}
          </span>
          {run.provider && (
            <span className="text-3 text-xs">
              {run.provider}{run.model ? ` · ${run.model}` : ''}
            </span>
          )}
        </div>

        {run.question && (
          <div style={{ padding: '8px 12px', background: 'var(--surface-2)', borderRadius: 'var(--r-sm)', fontSize: 13, marginBottom: 14 }}>
            <strong>Question:</strong> {run.question}
          </div>
        )}

        {run.error && (
          <Callout tone="warn">{run.error}</Callout>
        )}

        <AgentAnswerView answer={run.answer} />
      </GlassCard>

      <AgentRunDiff diff={run.diff} />

      {/* Execution Transcript Accordion */}
      {run.transcript?.length > 0 && (
        <Card pad title="Tool Execution Transcript" sub="Exact read-only tool invocations and their results">
          <Button size="xs" variant="secondary" onClick={() => setShowTranscript((v) => !v)}>
            {showTranscript ? 'Hide step details' : `Show ${run.transcript.length} reasoning steps`}
          </Button>

          {showTranscript && (
            <div className="flex-col gap-2" style={{ marginTop: 14 }}>
              {run.transcript.map((step, idx) => (
                <div key={step.index ?? idx} className="card" style={{ padding: '10px 14px', fontSize: 12.5, background: 'var(--surface-2)' }}>
                  <div className="flex items-center justify-between gap-2 flex-wrap" style={{ marginBottom: 6 }}>
                    <span className="font-mono font-bold brand">Step {(step.index ?? idx) + 1}</span>
                    <span className="text-3 text-xs">
                      {step.seconds != null ? `${step.seconds.toFixed(2)}s` : ''}
                    </span>
                  </div>

                  {step.thought && (
                    <div className="text-2" style={{ marginBottom: 6, lineHeight: 1.5, whiteSpace: 'normal' }}>
                      {step.thought}
                    </div>
                  )}

                  {step.calls?.length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
                      {step.calls.map((call, ci) => (
                        <Chip key={ci} tone="brand" size="sm" title={JSON.stringify(call.args || {})}>
                          {call.tool}
                        </Chip>
                      ))}
                    </div>
                  )}

                  {step.results?.length > 0 && (
                    <div className="trace-box">
                      {step.results.map((res, ri) => (
                        <div key={ri} className="trace-row">
                          <strong style={{ flexShrink: 0 }}>{res.tool}</strong>
                          <span>{summarise(res.result ?? res.error)}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {step.error && <Callout tone="neg">{step.error}</Callout>}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

/* Tool results are large nested objects; show a readable one-liner. */
function summarise(value) {
  if (value == null) return '—';
  if (typeof value !== 'object') return String(value);
  const text = JSON.stringify(value);
  return text.length > 400 ? `${text.slice(0, 400)}…` : text;
}

function History({ agent, onOpen, onClose, onDeleted }) {
  const toast = useToast();
  const { data, loading, refetch } = useQuery(
    `agent-runs:${agent.key}`, () => api.agentRuns(agent.key),
  );
  const runs = data?.runs || [];

  const remove = async (id) => {
    try {
      await api.deleteAgentRun(id);
      await refetch();
      onDeleted?.();
      toast.ok('Run deleted');
    } catch (e) {
      toast.fail('Could not delete run', e.message);
    }
  };

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
              className="card flex items-center justify-between gap-3 flex-wrap"
              style={{ padding: '12px 16px' }}
            >
              <button
                type="button"
                onClick={() => onOpen(r.id)}
                style={{
                  flex: 1, minWidth: 220, textAlign: 'left', background: 'none',
                  border: 'none', cursor: 'pointer', color: 'inherit', font: 'inherit', padding: 0,
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>
                  {r.answer?.headline || r.question || 'Agent run'}
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 2 }}>
                  {stampLabel(r.started_at)}
                  {r.seconds != null ? ` · ${r.seconds.toFixed(1)}s` : ''}
                  {r.status && r.status !== 'ok' ? ` · ${r.status}` : ''}
                </div>
              </button>
              <div className="flex items-center gap-2">
                <Button size="xs" variant="secondary" onClick={() => onOpen(r.id)}>View answer</Button>
                <ConfirmButton
                  size="xs"
                  variant="danger"
                  question="Delete this run permanently?"
                  confirmLabel="Delete"
                  onConfirm={() => remove(r.id)}
                >
                  Delete
                </ConfirmButton>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
