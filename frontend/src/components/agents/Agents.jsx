import React, { useCallback, useEffect, useState } from 'react';
import AgentAnswer from './AgentAnswer';
import JobProgress from '../JobProgress';
import { Callout, Card, Chip, ConfirmButton, Empty } from '../ui';
import { api, dateLabel, formatDuration, watchJob } from '../../lib';

/* Agents: a model that reads the ledger and answers a hard question about it.

   Every other tab answers a question somebody already knew to ask. This one is
   for the questions a person cannot phrase - "am I actually going to be short
   in March?", "which of these subscriptions is quietly the most expensive?" -
   and the way it works is by being handed the ledger and a job rather than an
   answer to narrate.

   The card is deliberately honest about that. It states the question the agent
   answers, what it will actually look at, and - once it has run - what it said
   and when. An agent whose last run was three weeks ago is worth re-running;
   one that ran this morning is not, and the card should make that obvious
   without being opened. */

const ICONS = {
  scale: '⚖️', drip: '💧', wave: '🌊', receipt: '🧾', shield: '🛡️',
};

function ago(iso) {
  if (!iso) return '';
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return '';
  const days = Math.floor((Date.now() - then.getTime()) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days} days ago`;
  return dateLabel(iso);
}

function AgentCard({ agent, disabled, running, onRun, onOpen, onHistory }) {
  const last = agent.last_run;
  return (
    <Card className="agent-card">
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <div style={{ fontSize: 24, lineHeight: 1 }} aria-hidden="true">
          {ICONS[agent.icon] || '🤖'}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15.5, fontWeight: 620 }}>{agent.name}</div>
          <div style={{ color: 'var(--text-2)', fontSize: 13.5, marginTop: 2 }}>
            {agent.question}
          </div>
        </div>
      </div>

      <p style={{ color: 'var(--text-3)', fontSize: 13, lineHeight: 1.6,
        margin: '10px 0 0' }}
      >
        {agent.blurb}
      </p>

      {last && last.headline && (
        /* The last answer, on the card. A verdict you have to click to see is
           a verdict you will not see. */
        <div
          role="button"
          tabIndex={0}
          onClick={onOpen}
          onKeyDown={(e) => (e.key === 'Enter' ? onOpen() : null)}
          style={{
            marginTop: 12, padding: '10px 12px', cursor: 'pointer',
            background: 'var(--surface-2)', borderRadius: 8,
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <div style={{ fontSize: 13.5, lineHeight: 1.5 }}>{last.headline}</div>
          <div style={{ color: 'var(--text-3)', fontSize: 12, marginTop: 4 }}>
            {ago(last.started_at)}
            {last.finding_count ? ` · ${last.finding_count} findings` : ''}
            {last.seconds ? ` · ${formatDuration(last.seconds)}` : ''}
          </div>
        </div>
      )}
      {last && !last.headline && (
        <div style={{ marginTop: 12 }}>
          <Chip tone="warn">
            last run {ago(last.started_at)} gave no answer
          </Chip>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
        <button className="btn primary" disabled={disabled || running}
          onClick={onRun}
        >
          {running ? 'Running…' : last ? 'Run again' : 'Run'}
        </button>
        {last && <button className="btn" onClick={onOpen}>Open last answer</button>}
        {last && <button className="btn" onClick={onHistory}>History</button>}
      </div>
    </Card>
  );
}

function History({ agent, onOpen, onClose, onDeleted }) {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    api.agentRuns(agent.key)
      .then((payload) => setRuns(payload.runs || []))
      .catch((e) => setError(e.message));
  }, [agent.key]);

  useEffect(load, [load]);

  return (
    <Card title={`${agent.name} — every run`}
      sub="An agent is worth re-running because the answer moves. This is where it moved."
    >
      <button className="btn" onClick={onClose}>← Back</button>
      {error && <Callout tone="neg" style={{ marginTop: 10 }}>{error}</Callout>}
      {!runs && <div className="spinner" style={{ marginTop: 14 }} />}
      {runs && !runs.length && (
        <Empty title="No runs yet">Run it once and this fills in.</Empty>
      )}
      {runs && runs.length > 0 && (
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>What it said</th>
                <th className="right">Findings</th>
                <th className="right">Took</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td className="nowrap">{dateLabel(run.started_at)}</td>
                  <td>
                    <button type="button" className="drill-link"
                      onClick={() => onOpen(run.id)}
                      style={{ textAlign: 'left' }}
                    >
                      {run.answer?.headline
                        || <em>{run.error || 'no answer'}</em>}
                    </button>
                  </td>
                  <td className="right num">
                    {(run.answer?.findings || []).length || '—'}
                  </td>
                  <td className="right num nowrap">
                    {formatDuration(run.seconds) || '—'}
                  </td>
                  <td className="right">
                    <ConfirmButton className="btn danger"
                      question="Delete this run? The comparison against it goes too."
                      confirmLabel="Delete"
                      onConfirm={() => api.deleteAgentRun(run.id)
                        .then(() => { load(); onDeleted?.(); })
                        .catch((e) => setError(e.message))}
                    >
                      Delete
                    </ConfirmButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function Agents() {
  const [catalogue, setCatalogue] = useState(null);
  const [error, setError] = useState(null);
  const [runningKey, setRunningKey] = useState(null);
  const [job, setJob] = useState(null);
  const [openRun, setOpenRun] = useState(null);
  const [historyFor, setHistoryFor] = useState(null);
  const [question, setQuestion] = useState({});

  const load = useCallback(() => {
    api.agents()
      .then((payload) => { setCatalogue(payload); setError(null); })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  async function open(runId) {
    setHistoryFor(null);
    try {
      const run = await api.agentRun(runId);
      setOpenRun(run);
    } catch (e) {
      setError(e.message);
    }
  }

  async function run(agent) {
    setError(null);
    setOpenRun(null);
    setHistoryFor(null);
    setRunningKey(agent.key);
    setJob(null);
    try {
      const { job_id: jobId } = await api.runAgent(
        agent.key, question[agent.key] || '');
      /* Watched rather than awaited. The run is several model round trips
         with tool execution between them, so the screen shows which step it
         is on - "Thinking (step 3 of 10)" is the difference between waiting
         and wondering whether anything is happening. */
      const finished = await watchJob(jobId, setJob);
      if (finished.status === 'failed') {
        throw new Error(finished.errors?.join('; ') || 'The run failed.');
      }
      if (finished.result?.run_id) await open(finished.result.run_id);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setRunningKey(null);
    }
  }

  if (!catalogue && !error) return <div className="spinner" style={{ margin: 40 }} />;

  const agents = catalogue?.agents || [];
  const blocked = catalogue && !catalogue.model_available;

  if (openRun) {
    return (
      <>
        {error && <Callout tone="neg">{error}</Callout>}
        <AgentAnswer run={openRun} onBack={() => setOpenRun(null)} />
      </>
    );
  }

  const historyAgent = agents.find((a) => a.key === historyFor);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>Agents</h2>
        <p style={{ color: 'var(--text-2)', margin: 0, maxWidth: 720,
          lineHeight: 1.6 }}
        >
          Every other tab answers a question you already knew to ask. These
          answer the ones you did not: each agent is handed your whole ledger
          and a job, decides for itself what to look at, and shows its working
          so every figure can be traced back to the rows it came from.
        </p>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}

      {blocked && (
        <Callout tone="warn">
          {catalogue.model_note}
          {' '}
          Unlike the rest of this app, an agent cannot fall back to a computed
          answer — choosing what to look at next is the whole of what it does.
        </Callout>
      )}

      {runningKey && job && (
        <Card title={agents.find((a) => a.key === runningKey)?.name}
          sub="It is reading your ledger and deciding what to look at next."
        >
          <JobProgress job={job} title={job.phase} showTrace={false} />
        </Card>
      )}

      {historyAgent ? (
        <History agent={historyAgent} onOpen={open}
          onClose={() => setHistoryFor(null)} onDeleted={load} />
      ) : (
        <div className="grid cols-2">
          {agents.map((agent) => (
            <div key={agent.key}>
              <AgentCard
                agent={agent}
                disabled={blocked}
                running={runningKey === agent.key}
                onRun={() => run(agent)}
                onOpen={() => open(agent.last_run.id)}
                onHistory={() => setHistoryFor(agent.key)}
              />
              {/* An optional steer, not a chat box. The agent has a job; this
                  points it at a corner of that job you care about, and left
                  empty it does what its card says. */}
              <input
                value={question[agent.key] || ''}
                placeholder="Optional: something specific to focus on"
                onChange={(e) => setQuestion(
                  (prev) => ({ ...prev, [agent.key]: e.target.value }))}
                style={{ width: '100%', marginTop: 8, fontSize: 13 }}
                disabled={blocked || runningKey === agent.key}
              />
            </div>
          ))}
        </div>
      )}

      {catalogue?.tools?.length > 0 && (
        <Card title="What an agent is allowed to touch"
          sub="Read-only, every one of them, and nothing outside this list. No agent can write to your ledger, change a category, or reach another account."
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {catalogue.tools.map((tool) => (
              <div key={tool.name} style={{ fontSize: 13, lineHeight: 1.5 }}>
                <code style={{ color: 'var(--text)' }}>{tool.name}</code>
                <span style={{ color: 'var(--text-3)' }}> — {tool.does}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
