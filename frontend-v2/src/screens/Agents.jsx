/* Agents: a model reads the ledger and answers a hard question about it.
 *
 * Every other screen answers a question somebody already knew to ask. This one
 * is for the questions a person cannot phrase - "am I actually going to be
 * short in March?", "which of these subscriptions is quietly the most
 * expensive?" - and the way it works is by being handed the ledger and a job
 * rather than an answer to narrate.
 *
 * A run is a JOB, not a request: several model round trips with tool execution
 * between them takes tens of seconds, and an HTTP request held open that long
 * dies to a proxy timeout, taking the analysis - the expensive part - with it.
 */

import React, { useState } from 'react';
import { api, watchJob } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { ago, dateLabel, duration, plural } from '../core/format';
import { Button, Callout, Card, Chip, ConfirmButton, Empty, Icon, Loading, Table } from '../ui';
import JobProgress from '../ui/JobProgress';

/* The catalogue names an icon; this maps it onto the app's own set, so a new
   agent arriving from the server renders as `sparkles` rather than as a gap. */
const ICONS = {
  scale: 'scales',       // Debt Strategist
  drip: 'repeat',        // Subscription & Leak Auditor
  wave: 'gauge',         // Cashflow Sentinel
  receipt: 'file',       // Tax Utilisation
  shield: 'shield',      // Emergency Fund & Resilience
  alarm: 'clock',        // Bill Shock Forecaster
  stairs: 'trending',    // Lifestyle Creep Detector
  gauge: 'target',       // Credit Health
  magnifier: 'search',   // Anomaly Watch
  coin: 'wallet',        // Fee & Waste Auditor
  pulse: 'briefcase',    // Income Stability
  scales: 'database',    // Ledger Trust
};

/* The figure check, read back off a stored run.
 *
 * The server does not return the unverified list as a field. It appends it to
 * the run's `error`, deliberately - an untraceable figure IS a defect in the
 * run, the same kind of thing as a step that failed, and belongs where a
 * reader scanning for "did this one go wrong" already looks. So this pulls the
 * two apart again for display: what actually broke, and which figures could
 * not be traced.
 *
 * Written against the exact string the repository composes. If that string
 * changes, `note` comes back null and the whole error is shown as-is, which is
 * the safe direction to fail in: the information is still on the screen. */
const FIGURE_NOTE = /^(\d+) figure\(s\) not traceable to a tool result:\s*(.*)$/;

/* The server states the same finding twice: once appended to `error`, once as
   a caveat on the answer. Both are machine-written, so when the block above
   the caveats is already showing it, the caveat is dropped rather than printed
   underneath in slightly different words. */
const FIGURE_CAVEAT = /figure\(s\) here did not come from any tool/;

function splitRunError(error) {
  if (!error) return { failure: '', note: null };
  const parts = String(error).split(' \u00b7 ');
  const failure = [];
  let note = null;
  for (const part of parts) {
    const m = FIGURE_NOTE.exec(part.trim());
    if (m) note = { count: Number(m[1]), figures: m[2].split(', ').filter(Boolean) };
    else failure.push(part);
  }
  return { failure: failure.join(' \u00b7 '), note };
}

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
    try { setOpenRun(await api.agentRun(runId)); }
    catch (e) { toast.fail('That answer could not be opened', e.message); }
  }

  async function run(agent) {
    setOpenRun(null);
    setHistoryFor(null);
    setRunningKey(agent.key);
    setJob(null);
    try {
      const { job_id: jobId } = await api.runAgent(agent.key, question[agent.key] || '');
      /* Watched rather than awaited: the run is several model round trips with
         tool execution between them, so the screen shows which step it is on.
         "Thinking (step 3 of 10)" is the difference between waiting and
         wondering whether anything is happening. */
      const finished = await watchJob(jobId, setJob);
      if (finished.status === 'failed') {
        throw new Error(finished.errors?.join('; ') || 'The run failed.');
      }
      if (finished.result?.run_id) await open(finished.result.run_id);
      refetch();
    } catch (e) {
      toast.fail('That agent did not finish', e.message);
    } finally { setRunningKey(null); }
  }

  if (loading) return <Loading label="Reading the agent catalogue…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  const agents = catalogue?.agents || [];
  const blocked = catalogue && !catalogue.model_available;

  if (openRun) {
    return <AgentAnswer run={openRun} onBack={() => setOpenRun(null)} />;
  }

  const historyAgent = agents.find((a) => a.key === historyFor);

  return (
    <>
      <div>
        <h2 className="h2">Agents</h2>
        <p className="lead">
          Every other screen answers a question you already knew to ask. These answer the
          ones you did not: each agent is handed your whole ledger and a job, decides for
          itself what to look at, and shows its working so every figure can be traced
          back to the rows it came from.
        </p>
      </div>

      {/* Which budget is in force. Said out loud rather than left to be
          inferred, because a compact run and a broken run look identical from
          outside — three findings instead of six — and a reader who cannot
          tell them apart will read a working agent as a poor one. */}
      {!blocked && catalogue?.profile?.name === 'compact' && (
        <Callout icon="gauge">
          <strong>Compact budget</strong>
          <span className="dim"> · {catalogue.profile.max_steps} steps per run</span>
          <p style={{ margin: '4px 0 0', lineHeight: 1.55 }}>{catalogue.profile.note}</p>
        </Callout>
      )}

      {blocked && (
        <Callout tone="warn">
          {catalogue.model_note} Unlike the rest of this app, an agent cannot fall back to
          a computed answer — choosing what to look at next is the whole of what it does.
        </Callout>
      )}

      {runningKey && job && (
        <Card title={agents.find((a) => a.key === runningKey)?.name}
          sub="It is reading your ledger and deciding what to look at next.">
          <JobProgress job={job} title={job.phase} trace={false} />
        </Card>
      )}

      {historyAgent ? (
        <History agent={historyAgent} onOpen={open} onClose={() => setHistoryFor(null)}
          onDeleted={refetch} />
      ) : (
        <div className="grid cols-2 agent-grid">
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

      {catalogue?.tools?.length > 0 && (
        <Card
          title="What an agent is allowed to touch"
          sub="Read-only, every one of them, and nothing outside this list. No agent can write to your ledger, change a category, or reach another account."
        >
          <div className="col" style={{ gap: 5 }}>
            {catalogue.tools.map((tool) => (
              <div key={tool.name} className="small">
                <code>{tool.name}</code> <span className="dim">— {tool.does}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}

function AgentCard({ agent, disabled, running, question, onQuestion,
                    onRun, onOpen, onHistory }) {
  const last = agent.last_run;
  const { note } = splitRunError(last?.error);
  /* pad={false} because the body below is the card's own `.card-body`. Letting
     Card add one as well nests two of them and pads the card twice. */
  return (
    <Card className="agent-card" pad={false}>
      <div className="card-body">
        <div className="row" style={{ alignItems: 'flex-start' }}>
          <span className="empty-mark" style={{ width: 34, height: 34, marginBottom: 0 }}>
            <Icon name={ICONS[agent.icon] || 'sparkles'} size={16} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 630 }}>{agent.name}</div>
            <div className="small muted">{agent.question}</div>
          </div>
        </div>

        <p className="small dim" style={{ margin: '10px 0 0', lineHeight: 1.6 }}>
          {agent.blurb}
        </p>

        {/* The last answer, on the card. A verdict you have to click to see is
            a verdict you will not see. */}
        {last?.headline && (
          <button
            type="button"
            onClick={onOpen}
            style={{
              display: 'block', width: '100%', textAlign: 'left', marginTop: 12,
              padding: '10px 12px', cursor: 'pointer', font: 'inherit', color: 'inherit',
              background: 'var(--surface-2)', border: 0,
              borderLeft: '3px solid var(--accent)', borderRadius: 'var(--r-sm)',
            }}
          >
            <div style={{ fontSize: 13.5, lineHeight: 1.5 }}>{last.headline}</div>
            <div className="tiny dim" style={{ marginTop: 4 }}>
              {ago(last.started_at)}
              {last.finding_count ? ` · ${last.finding_count} findings` : ''}
              {last.seconds ? ` · ${duration(last.seconds)}` : ''}
            </div>
            {/* On the card, because this is where the last answer is read. A
                headline with an untraceable figure in it should not look like
                a headline without one. */}
            {note && (
              <div style={{ marginTop: 6 }}>
                <Chip tone="neg">{plural(note.count, 'figure')} not traced</Chip>
              </div>
            )}
          </button>
        )}
        {last && !last.headline && (
          <div style={{ marginTop: 12 }}>
            <Chip tone="warn">last run {ago(last.started_at)} gave no answer</Chip>
          </div>
        )}

        {/* An optional steer, not a chat box. The agent has a job; this points
            it at a corner of that job you care about, and left empty it does
            what its card says. Inside the card, with the buttons, because it
            belongs to this agent - floated underneath it read as an unrelated
            box sitting between two rows. */}
        <div className="agent-actions">
          <input
            value={question}
            placeholder="Optional: something specific to focus on"
            onChange={(e) => onQuestion(e.target.value)}
            disabled={disabled || running}
          />
          <div className="row" style={{ marginTop: 8 }}>
            <Button variant="primary" icon="play" disabled={disabled || running} onClick={onRun}>
              {running ? 'Running…' : last ? 'Run again' : 'Run'}
            </Button>
            {last && <Button onClick={onOpen}>Open last answer</Button>}
            {last && <Button onClick={onHistory}>History</Button>}
          </div>
        </div>
      </div>
    </Card>
  );
}

function History({ agent, onOpen, onClose, onDeleted }) {
  const { data, loading, error, refetch } = useQuery(
    `agent-runs:${agent.key}`, () => api.agentRuns(agent.key));
  const runs = data?.runs || [];

  return (
    <Card
      title={`${agent.name} — every run`}
      sub="An agent is worth re-running because the answer moves. This is where it moved."
      tools={<Button size="sm" icon="arrow-left" onClick={onClose}>Back</Button>}
      pad={false}
    >
      {error && <div style={{ padding: 16 }}><Callout tone="neg">{error.message}</Callout></div>}
      {loading && <Loading />}
      {!loading && !runs.length && (
        <div style={{ padding: 16 }}>
          <Empty title="No runs yet" icon="sparkles">Run it once and this fills in.</Empty>
        </div>
      )}
      {runs.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th>When</th><th>What it said</th>
              <th className="right">Findings</th><th className="right">Took</th><th />
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              /* An untraceable figure arrives appended to `error`, on a run
                 whose status is otherwise `ok`. Shown here rather than only
                 inside the run, because the point of putting it in `error`
                 is that somebody scanning the list for "did one of these go
                 wrong" should not have to open twenty runs to find out. */
              const { failure, note } = splitRunError(run.error);
              return (
              <tr key={run.id}>
                <td className="nowrap">{dateLabel(run.started_at)}</td>
                <td>
                  <button type="button" className="drill" style={{ textAlign: 'left' }}
                    onClick={() => onOpen(run.id)}>
                    {run.answer?.headline || <em>{failure || 'no answer'}</em>}
                  </button>
                  {note && (
                    <div style={{ marginTop: 4 }}>
                      <Chip tone="neg">{plural(note.count, 'figure')} not traced</Chip>
                    </div>
                  )}
                </td>
                <td className="right num">{(run.answer?.findings || []).length || '—'}</td>
                <td className="right num nowrap">{duration(run.seconds) || '—'}</td>
                <td className="right">
                  <ConfirmButton
                    size="xs" variant="danger"
                    question="Delete this run? The comparison against it goes too."
                    confirmLabel="Delete"
                    onConfirm={() => api.deleteAgentRun(run.id)
                      .then(() => { refetch(); onDeleted?.(); })}
                  >
                    Delete
                  </ConfirmButton>
                </td>
              </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

/* ── one run, rendered ───────────────────────────────────────────────────── */

/* Order is the argument. The headline first, because an agent that made you
   read to find the point has wasted the run. Then what CHANGED since last
   time, which is the whole reason to re-run something. Then the findings, then
   what could be done about them, and the caveats last but never folded away,
   because a figure whose limits are hidden is worse than no figure. */

const SEVERITY = { urgent: ['neg', 'Urgent'], watch: ['warn', 'Watch'], info: ['', 'Note'] };
const EFFORT = { low: 'Easy', medium: 'Some work', high: 'A project' };

function AgentAnswer({ run, onBack }) {
  const answer = run.answer || {};
  const failed = run.status === 'failed';
  const exhausted = run.status === 'exhausted';
  /* The figure check rides in with the error; see splitRunError. Doing this
     here rather than at each use keeps a failure and an untraceable figure
     from being reported as one thing, which is how they arrive. */
  const { failure, note } = splitRunError(run.error);
  const caveats = (answer.caveats || []).filter((c) => !(note && FIGURE_CAVEAT.test(c)));

  return (
    <>
      <div className="row" style={{ alignItems: 'baseline' }}>
        <Button icon="arrow-left" onClick={onBack}>All agents</Button>
        <div>
          <h2 className="h2">{run.agent_name || run.agent}</h2>
          <div className="tiny dim">
            {run.question || run.agent_question}
            {run.started_at ? ` · ran ${dateLabel(run.started_at)}` : ''}
            {run.seconds ? ` in ${duration(run.seconds)}` : ''}
            {/* Steps as well as tool calls, because the step count is what
                says which budget this particular run had - and an old run
                under a different budget is exactly when that matters. */}
            {run.steps ? ` · ${plural(run.steps, 'step')}` : ''}
            {run.tool_calls ? ` · ${run.tool_calls} tool calls` : ''}
          </div>
        </div>
      </div>

      {failed && <Callout tone="neg">This run did not finish: {failure || run.error}</Callout>}
      {exhausted && (
        <Callout tone="warn">
          The agent used all its steps without settling on an answer. Its working is
          below — that is often enough to see what it was chasing, and running it again
          usually gets further.
        </Callout>
      )}

      {answer.headline && (
        <Card pad>
          <div style={{ fontSize: 19, fontWeight: 630, letterSpacing: '-.02em', lineHeight: 1.35 }}>
            {answer.headline}
          </div>
          {answer.summary && (
            <p className="prose" style={{ marginTop: 10 }}>{answer.summary}</p>
          )}
        </Card>
      )}

      {answer.metrics?.length > 0 && (
        <div className="grid cols-3">
          {answer.metrics.map((m) => (
            <div className="stat" key={m.label}>
              <div className="stat-label">{m.label}</div>
              <div className="stat-value">
                {m.value}
                {m.unit && m.unit !== 'INR' && (
                  <span className="dim" style={{ fontSize: 14, marginLeft: 3 }}>
                    {m.unit === '%' ? '%' : ` ${m.unit}`}
                  </span>
                )}
              </div>
              {m.note && <div className="stat-note">{m.note}</div>}
            </div>
          ))}
        </div>
      )}

      <Changes diff={run.diff} previous={run.previous} />

      {answer.findings?.length > 0 && (
        <Card title="What it found" pad>
          <div className="col" style={{ gap: 14 }}>
            {answer.findings.map((f, i) => {
              const [tone, label] = SEVERITY[f.severity] || SEVERITY.info;
              return (
                <div key={i} style={{
                  borderLeft: `3px solid var(--${f.severity === 'urgent' ? 'neg'
                    : f.severity === 'watch' ? 'warn' : 'line-strong'})`,
                  paddingLeft: 14,
                }}>
                  <div className="row tight">
                    <strong style={{ fontSize: 14.5 }}>{f.title}</strong>
                    {f.severity !== 'info' && <Chip tone={tone}>{label}</Chip>}
                  </div>
                  <p className="muted" style={{ fontSize: 14, lineHeight: 1.6, marginTop: 4 }}>
                    {f.detail}
                  </p>
                  {f.evidence?.length > 0 && (
                    <div className="row tight" style={{ marginTop: 6 }}>
                      {f.evidence.map((e, j) => <Chip key={j} className="num">{e}</Chip>)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {answer.actions?.length > 0 && (
        <Card
          title="What would change it"
          sub="The mechanics of each option, with the arithmetic. Which of them is worth doing is your call — this app does not know what else your money is for."
          pad
        >
          <div className="col" style={{ gap: 14 }}>
            {answer.actions.map((a, i) => (
              <div key={i}>
                <div className="row tight">
                  <strong style={{ fontSize: 14.5 }}>{a.title}</strong>
                  {a.effort && <Chip>{EFFORT[a.effort] || a.effort}</Chip>}
                </div>
                {a.detail && (
                  <p className="muted" style={{ fontSize: 14, lineHeight: 1.6, marginTop: 4 }}>
                    {a.detail}
                  </p>
                )}
                {a.mechanism && a.mechanism !== 'n/a' && (
                  <p className="small dim" style={{ marginTop: 4 }}>
                    <strong className="muted">What changes: </strong>{a.mechanism}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Figures no tool produced.
          Its own block, above the caveats rather than inside them, because it
          is a different kind of statement: a caveat qualifies an answer, this
          says a named number in it could not be traced back to anything.
          Nothing is edited out of the prose above - a sentence quietly
          rewritten would read as though it had been checked, and being able
          to tell those apart is the entire point of checking. */}
      {note && (
        <Callout tone="neg">
          <strong>
            {plural(note.count, 'figure')} could not be traced
          </strong>
          <p style={{ margin: '4px 0 8px', lineHeight: 1.55 }}>
            No tool call in this run returned {note.count > 1 ? 'these' : 'this'}. They may
            be arithmetic the model did itself, or they may simply be wrong — check them
            against the working below before relying on them.
          </p>
          <div className="row tight">
            {note.figures.map((figure) => (
              <Chip key={figure} tone="neg" className="num">{figure}</Chip>
            ))}
          </div>
        </Callout>
      )}

      {caveats.length > 0 && (
        <Callout tone="warn">
          <strong>Worth knowing about these figures</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {caveats.map((c, i) => <li key={i} style={{ listStyle: 'disc' }}>{c}</li>)}
          </ul>
        </Callout>
      )}

      <Working runId={run.id} />
    </>
  );
}

/* What moved since the previous run. Computed on the server from the two
   answers and rendered above the findings: somebody re-running an agent
   already knows roughly what it says, and the delta is the only genuinely new
   information on the page. */
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
    <Card
      title="What changed"
      sub={previous?.started_at
        ? `Against the run on ${dateLabel(previous.started_at)}` : 'Against the previous run'}
      pad
    >
      {moved.length > 0 && (
        <div className="row" style={{ marginBottom: 12 }}>
          {moved.map((m) => (
            <div key={m.label} className="card sunken" style={{ padding: '8px 12px', minWidth: 160 }}>
              <div className="tiny dim">{m.label}</div>
              <div className="num" style={{ fontSize: 15, fontWeight: 620 }}>
                {m.then} → {m.now}
                <span style={{
                  marginLeft: 6, fontSize: 12,
                  color: m.direction === 'up' ? 'var(--neg)' : 'var(--pos)',
                }}>
                  {m.direction === 'up' ? '▲' : '▼'} {Math.abs(m.delta)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="row tight">
        {isNew.map((t) => <Chip key={t} tone="warn">new · {t}</Chip>)}
        {gone.map((t) => <Chip key={t} tone="pos">gone · {t}</Chip>)}
        {diff.unchanged_findings > 0 && <Chip>{diff.unchanged_findings} unchanged</Chip>}
      </div>
    </Card>
  );
}

/* The agent's working: every tool it called and what came back.
 *
 * Collapsed by default and never omitted. An agent's numbers are only worth
 * anything if they can be traced, and this is where a figure in a finding is
 * checked against the call that produced it. Fetched on open, because the
 * transcript is by far the largest thing in the record. */
function Working({ runId }) {
  const [open, setOpen] = useState(false);
  const { data, loading, error } = useQuery(
    open ? `agent-transcript:${runId}` : null,
    () => api.agentRun(runId, { transcript: true }),
  );
  const steps = data?.transcript || [];

  return (
    <Card
      title="How it got there"
      sub="Every tool call and every figure it read. The numbers above come from here."
      pad
    >
      <Button icon={open ? 'eye-off' : 'eye'} onClick={() => setOpen((v) => !v)}>
        {open ? 'Hide the working' : 'Show the working'}
      </Button>
      {error && <Callout tone="neg" style={{ marginTop: 10 }}>{error.message}</Callout>}
      {open && loading && <Loading />}
      {open && steps.length > 0 && (
        <div className="col" style={{ marginTop: 12, gap: 10 }}>
          {steps.map((step) => (
            <div key={step.index} style={{
              borderLeft: '2px solid var(--line)', paddingLeft: 12,
            }}>
              <div className="small muted">
                <strong>Step {step.index}</strong>
                {step.seconds ? ` · ${duration(step.seconds)}` : ''}
                {step.thought ? ` — ${step.thought}` : ''}
              </div>
              {step.error && <div className="tiny neg">{step.error}</div>}
              {(step.results || []).map((r, i) => (
                <details key={i} style={{ marginTop: 6 }}>
                  <summary style={{ cursor: 'pointer', fontSize: 12.5 }}>
                    <code>{r.tool}</code>
                    {step.calls?.[i]?.args && Object.keys(step.calls[i].args).length > 0
                      ? ` (${JSON.stringify(step.calls[i].args).slice(0, 90)})` : ''}
                  </summary>
                  <pre className="code-block" style={{ maxHeight: 260, overflow: 'auto' }}>
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
