import React, { useEffect, useState } from 'react';
import { Callout, Card, Chip } from '../ui';
import { api, dateLabel, formatDuration } from '../../lib';

/* One agent run, rendered.

   Order is the argument. The headline first, because an agent that made you
   read to find the point has wasted the run. Then what CHANGED since last
   time, which is the whole reason to re-run something - "your EMIs are 43% of
   take-home" is a fact any screen can show, "they were 47% in March" is not.
   Then the findings, then what could be done about them, and the caveats last
   but never folded away, because a figure whose limits are hidden is worse
   than no figure. */

const SEVERITY = {
  urgent: ['neg', 'Urgent'],
  watch: ['warn', 'Watch'],
  info: ['', 'Note'],
};

const EFFORT = { low: 'Easy', medium: 'Some work', high: 'A project' };

function Metric({ metric }) {
  return (
    <div className="card stat">
      <div className="stat-label">{metric.label}</div>
      <div className="stat-value num">
        {metric.value}
        {metric.unit && metric.unit !== 'INR' && (
          <span style={{ fontSize: 14, color: 'var(--text-3)', marginLeft: 3 }}>
            {metric.unit === '%' ? '%' : ` ${metric.unit}`}
          </span>
        )}
      </div>
      {metric.note && <div className="stat-note">{metric.note}</div>}
    </div>
  );
}

/* What moved since the previous run.

   Computed on the server from the two answers, and rendered above the
   findings rather than below them: somebody re-running an agent already knows
   roughly what it says, and the delta is the only genuinely new information
   on the page. */
function Changes({ diff, previous }) {
  if (!diff?.available) return null;
  const moved = diff.metrics_moved || [];
  const isNew = diff.new_findings || [];
  const gone = diff.resolved_findings || [];
  if (!moved.length && !isNew.length && !gone.length) {
    return (
      <Callout>
        Nothing material has changed since the last run
        {previous?.started_at ? ` on ${dateLabel(previous.started_at)}` : ''}.
      </Callout>
    );
  }
  return (
    <Card title="What changed"
      sub={previous?.started_at
        ? `Against the run on ${dateLabel(previous.started_at)}`
        : 'Against the previous run'}
    >
      {moved.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 12 }}>
          {moved.map((m) => (
            <div key={m.label} style={{
              border: '1px solid var(--surface-2)', borderRadius: 8,
              padding: '8px 12px', minWidth: 160,
            }}
            >
              <div style={{ fontSize: 12, color: 'var(--text-3)' }}>{m.label}</div>
              <div className="num" style={{ fontSize: 15, fontWeight: 600 }}>
                {m.then} → {m.now}
                <span style={{
                  marginLeft: 6, fontSize: 12,
                  color: m.direction === 'up' ? 'var(--negative)' : 'var(--positive)',
                }}
                >
                  {m.direction === 'up' ? '▲' : '▼'} {Math.abs(m.delta)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {isNew.map((t) => <Chip key={t} tone="warn">new · {t}</Chip>)}
        {gone.map((t) => <Chip key={t} tone="pos">gone · {t}</Chip>)}
        {diff.unchanged_findings > 0 && (
          <Chip>{diff.unchanged_findings} unchanged</Chip>
        )}
      </div>
    </Card>
  );
}

/* The agent's working: every tool it called and what came back.

   Collapsed by default and never omitted. An agent's numbers are only worth
   anything if they can be traced, and this is where a figure in a finding is
   checked against the call that produced it. Fetched on open, because the
   transcript is by far the largest thing in the record. */
function Working({ runId }) {
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open || steps) return;
    api.agentRun(runId, { transcript: true })
      .then((run) => setSteps(run.transcript || []))
      .catch((e) => setError(e.message));
  }, [open, steps, runId]);

  return (
    <Card title="How it got there"
      sub="Every tool call and every figure it read. The numbers above come from here."
    >
      <button className="btn" onClick={() => setOpen((v) => !v)}>
        {open ? 'Hide the working' : 'Show the working'}
      </button>
      {error && <Callout tone="neg" style={{ marginTop: 10 }}>{error}</Callout>}
      {open && !steps && !error && <div className="spinner" style={{ marginTop: 12 }} />}
      {open && steps && (
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {steps.map((step) => (
            <div key={step.index} style={{
              borderLeft: '2px solid var(--surface-2)', paddingLeft: 12,
            }}
            >
              <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
                <strong>Step {step.index}</strong>
                {step.seconds ? ` · ${formatDuration(step.seconds)}` : ''}
                {step.thought ? ` — ${step.thought}` : ''}
              </div>
              {step.error && (
                <div style={{ fontSize: 12.5, color: 'var(--negative)' }}>
                  {step.error}
                </div>
              )}
              {(step.results || []).map((r, i) => (
                <details key={i} style={{ marginTop: 6 }}>
                  <summary style={{ cursor: 'pointer', fontSize: 12.5 }}>
                    <code>{r.tool}</code>
                    {(step.calls?.[i]?.args
                      && Object.keys(step.calls[i].args).length > 0)
                      ? ` (${JSON.stringify(step.calls[i].args).slice(0, 90)})`
                      : ''}
                  </summary>
                  <pre style={{
                    fontSize: 11.5, overflowX: 'auto', maxHeight: 260,
                    background: 'var(--surface-2)', padding: 10,
                    borderRadius: 6, margin: '6px 0 0',
                  }}
                  >
                    {JSON.stringify(r.result, null, 1)}
                  </pre>
                </details>
              ))}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function AgentAnswer({ run, onBack }) {
  if (!run) return null;
  const answer = run.answer || {};
  const failed = run.status === 'failed';
  const exhausted = run.status === 'exhausted';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        {onBack && (
          <button className="btn" onClick={onBack}>← All agents</button>
        )}
        <div>
          <h2 className="section-title" style={{ marginBottom: 2 }}>
            {run.agent_name || run.agent}
          </h2>
          <div style={{ color: 'var(--text-3)', fontSize: 13 }}>
            {run.question || run.agent_question}
            {run.started_at ? ` · ran ${dateLabel(run.started_at)}` : ''}
            {run.seconds ? ` in ${formatDuration(run.seconds)}` : ''}
            {run.tool_calls ? ` · ${run.tool_calls} tool calls` : ''}
            {run.figures_checked
              ? ` · ${run.figures_checked} figure${run.figures_checked > 1 ? 's' : ''} checked`
              : ''}
          </div>
        </div>
      </div>

      {failed && (
        <Callout tone="neg">
          This run did not finish: {run.error}
        </Callout>
      )}
      {exhausted && (
        <Callout tone="warn">
          The agent used all its steps without settling on an answer. Its
          working is below — that is often enough to see what it was chasing,
          and running it again usually gets further.
        </Callout>
      )}

      {answer.headline && (
        <Card>
          <div style={{ fontSize: 19, fontWeight: 620, letterSpacing: '-.3px',
            lineHeight: 1.35 }}
          >
            {answer.headline}
          </div>
          {answer.summary && (
            <p style={{ color: 'var(--text-2)', fontSize: 14.5, lineHeight: 1.65,
              margin: '10px 0 0' }}
            >
              {answer.summary}
            </p>
          )}
        </Card>
      )}

      {answer.metrics?.length > 0 && (
        <div className="grid cols-3">
          {answer.metrics.map((m) => <Metric key={m.label} metric={m} />)}
        </div>
      )}

      <Changes diff={run.diff} previous={run.previous} />

      {answer.findings?.length > 0 && (
        <Card title="What it found">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {answer.findings.map((f, i) => {
              const [tone, label] = SEVERITY[f.severity] || SEVERITY.info;
              return (
                <div key={i} style={{
                  borderLeft: `3px solid var(--${
                    f.severity === 'urgent' ? 'negative'
                      : f.severity === 'watch' ? 'warning' : 'surface-2'})`,
                  paddingLeft: 14,
                }}
                >
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center',
                    flexWrap: 'wrap' }}
                  >
                    <strong style={{ fontSize: 14.5 }}>{f.title}</strong>
                    {f.severity !== 'info' && <Chip tone={tone}>{label}</Chip>}
                  </div>
                  <p style={{ color: 'var(--text-2)', fontSize: 14,
                    lineHeight: 1.6, margin: '5px 0 0' }}
                  >
                    {f.detail}
                  </p>
                  {f.evidence?.length > 0 && (
                    <div style={{ marginTop: 6, display: 'flex', gap: 6,
                      flexWrap: 'wrap' }}
                    >
                      {f.evidence.map((e, j) => (
                        <span key={j} className="num" style={{
                          fontSize: 12, color: 'var(--text-3)',
                          background: 'var(--surface-2)', padding: '2px 7px',
                          borderRadius: 5,
                        }}
                        >
                          {e}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {answer.actions?.length > 0 && (
        <Card title="What would change it"
          sub="The mechanics of each option, with the arithmetic. Which of them is worth doing is your call — this app does not know what else your money is for."
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {answer.actions.map((a, i) => (
              <div key={i}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center',
                  flexWrap: 'wrap' }}
                >
                  <strong style={{ fontSize: 14.5 }}>{a.title}</strong>
                  {a.effort && <Chip>{EFFORT[a.effort] || a.effort}</Chip>}
                </div>
                {a.detail && (
                  <p style={{ color: 'var(--text-2)', fontSize: 14,
                    lineHeight: 1.6, margin: '5px 0 0' }}
                  >
                    {a.detail}
                  </p>
                )}
                {a.mechanism && a.mechanism !== 'n/a' && (
                  <p style={{ color: 'var(--text-3)', fontSize: 13,
                    lineHeight: 1.55, margin: '4px 0 0' }}
                  >
                    <strong style={{ color: 'var(--text-2)' }}>What changes: </strong>
                    {a.mechanism}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Figures the tools never produced.

          Shown as its own block rather than folded into the caveats, and
          above them, because it is a different kind of statement: a caveat
          qualifies an answer, this says a specific number in it could not be
          traced. Nothing is deleted - editing the prose would leave a
          sentence that reads as if it had been checked, and being able to
          tell the difference is the entire point. */}
      {run.unverified?.length > 0 && (
        <Callout tone="neg">
          <strong>
            {run.unverified.length} figure
            {run.unverified.length > 1 ? 's' : ''} could not be traced
          </strong>
          <p style={{ margin: '4px 0 8px', lineHeight: 1.55 }}>
            No tool call in this run returned{' '}
            {run.unverified.length > 1 ? 'these' : 'this'}. They may be
            arithmetic the model did itself, or they may simply be wrong —
            check them against the working below before relying on them.
          </p>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {run.unverified.map((figure) => (
              <span key={figure} className="num" style={{
                fontSize: 13, background: 'var(--negative-soft)',
                color: 'var(--negative)', padding: '2px 8px', borderRadius: 5,
              }}
              >
                {figure}
              </span>
            ))}
          </div>
        </Callout>
      )}

      {answer.caveats?.length > 0 && (
        <Callout tone="warn">
          <strong>Worth knowing about these figures</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18, lineHeight: 1.6 }}>
            {answer.caveats.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </Callout>
      )}

      <Working runId={run.id} />
    </div>
  );
}
