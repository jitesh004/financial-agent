/* ────────────────────────────────────────────────────────────────────────────
   Ask the ledger.

   Each turn is a full agent run - tools called, every figure checked back
   against what those tools returned - so a conversational answer is held
   to the same standard as a specialist report. The thread is what makes
   "and in July?" answerable.

   Two things this screen refuses to hide:

     - The working. Every answer can be opened to show which tools ran and
       what they returned, because an unopenable number is a number you
       have to take on faith.
     - An unverified figure. If the model stated something no tool
       produced, that is said on the answer rather than left for the
       reader to catch.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useEffect, useRef, useState } from 'react';
import { api } from '../core/api';
import { invalidate, useJobWatch, useQuery } from '../core/store';
import { useToast } from '../core/toast';
import {
  Badge, Button, Callout, Card, Chip, Empty, Loading,
} from '../ui';
import { AgentAnswerView } from '../ui/AgentAnswerView';
import JobProgress from '../ui/JobProgress';

const SUGGESTIONS = [
  'What did I spend the most on last month?',
  'How much am I paying in subscriptions?',
  'What is my biggest recurring charge?',
  'How much did I earn in August 2026?',
];

export default function Chat() {
  const toast = useToast();
  const [conversationId, setConversationId] = useState('');
  const [draft, setDraft] = useState('');
  const [jobId, setJobId] = useState('');
  const bottomRef = useRef(null);

  const { data: list, refetch: refetchList } = useQuery(
    'conversations', () => api.conversations());

  const { data: thread, loading, refetch: refetchThread } = useQuery(
    conversationId ? `conversation:${conversationId}` : null,
    () => api.conversation(conversationId),
    { enabled: Boolean(conversationId) },
  );

  const job = useJobWatch(jobId);
  const running = Boolean(jobId && job && job.active);

  /* The turn has landed - pull it in and stop watching. */
  useEffect(() => {
    if (!jobId || !job || job.active) return;
    setJobId('');
    refetchThread();
    refetchList();
    if (job.status === 'failed') {
      toast.fail('That question could not be answered',
        job.errors?.[0] || job.message || '');
    }
  }, [job, jobId, refetchThread, refetchList, toast]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [thread?.turns?.length, running]);

  async function ask(question) {
    const text = (question ?? draft).trim();
    if (!text || running) return;
    setDraft('');
    try {
      const r = await api.askChat(text, conversationId || undefined);
      setConversationId(r.conversation_id);
      setJobId(r.job_id);
      invalidate('conversations');
    } catch (e) {
      toast.fail('Could not ask that', e.message);
    }
  }

  const turns = thread?.turns || [];
  const conversations = list?.conversations || [];

  return (
    <div className="page-enter" style={{
      display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 240px',
      gap: 'var(--space-5)', alignItems: 'start',
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        <div className="page-head" style={{ marginBottom: 0 }}>
          <div>
            <h1 className="h1" style={{ margin: 0 }}>Ask</h1>
            <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
              Questions about your own ledger. Every figure comes from a
              query you can open and read &mdash; nothing here is estimated.
            </p>
          </div>
        </div>

        {!conversationId && !turns.length && (
          <Card pad>
            <Empty title="Ask anything about your money" icon="sparkles">
              The copilot reads your ledger with the same tools the reports
              use, and shows its working.
            </Empty>
            <div className="flex flex-wrap gap-2" style={{ marginTop: 12 }}>
              {SUGGESTIONS.map((s) => (
                <Button key={s} size="sm" variant="ghost" onClick={() => ask(s)}>
                  {s}
                </Button>
              ))}
            </div>
          </Card>
        )}

        {loading && conversationId && !turns.length && (
          <Loading message="Opening that conversation…" />
        )}

        {turns.map((turn) => <Turn key={turn.id} turn={turn} />)}

        {running && (
          <Card pad>
            <JobProgress job={job} title={job?.phase || 'Thinking…'} trace
              traceHeight={220} />
          </Card>
        )}

        <div ref={bottomRef} />

        {/* The composer */}
        <Card pad>
          <div className="flex items-center gap-2">
            <input
              className="input"
              style={{ flex: 1 }}
              value={draft}
              placeholder={turns.length
                ? 'Ask a follow-up…'
                : 'Ask anything about your finances…'}
              disabled={running}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') ask(); }}
            />
            <Button variant="primary" busy={running}
              disabled={running || !draft.trim()} onClick={() => ask()}>
              Ask
            </Button>
          </div>
          {turns.length > 0 && (
            <div className="tiny muted" style={{ marginTop: 6 }}>
              A follow-up can refer to what was just said &mdash; &ldquo;and
              in July?&rdquo; works. Figures are always re-queried, never
              carried over.
            </div>
          )}
        </Card>
      </div>

      {/* Conversations */}
      <Card pad title="Conversations">
        <Button size="sm" variant="ghost" style={{ width: '100%', marginBottom: 8 }}
          onClick={() => { setConversationId(''); setDraft(''); }}>
          + New
        </Button>
        {!conversations.length && (
          <span className="tiny muted">Nothing asked yet.</span>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {conversations.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setConversationId(c.id)}
              title={c.title}
              style={{
                textAlign: 'left', padding: '7px 9px', fontSize: 12.5,
                borderRadius: 'var(--radius-sm)', cursor: 'pointer',
                border: '1px solid transparent',
                background: c.id === conversationId ? 'var(--accent-soft)' : 'transparent',
                color: c.id === conversationId ? 'var(--accent-text)' : 'var(--text-2)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}
            >
              {c.title || 'Untitled'}
              <span className="tiny" style={{ opacity: 0.6 }}> · {c.turns}</span>
            </button>
          ))}
        </div>
      </Card>
    </div>
  );
}

function Turn({ turn }) {
  const [showWorking, setShowWorking] = useState(false);
  const answer = turn.answer || {};
  const failed = turn.status !== 'ok';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* The question */}
      <div style={{ alignSelf: 'flex-end', maxWidth: '80%' }}>
        <div style={{
          padding: '9px 13px', borderRadius: 'var(--radius-md)',
          background: 'var(--accent-soft)', color: 'var(--text)',
          fontSize: 13.5, border: '1px solid var(--accent-border)',
        }}>
          {turn.question}
        </div>
      </div>

      {/* The answer */}
      <Card pad>
        {failed ? (
          <Callout tone="neg">
            {turn.error || 'That question did not reach an answer.'}
          </Callout>
        ) : (
          <>
            {/* The same renderer the Agents hub and the Copilot drawer use.
                Not re-implemented here on purpose: it already puts the
                "figures that did not come from your ledger" banner ABOVE
                the headline, and a second rendering of an answer is a
                second place for that warning to get lost. */}
            <AgentAnswerView answer={answer} compact />

            {turn.run_id && (
              <div style={{ marginTop: 10 }}>
                <Button size="xs" variant="ghost"
                  onClick={() => setShowWorking(!showWorking)}>
                  {showWorking ? 'Hide working' : 'Show working'}
                </Button>
                {showWorking && <Working runId={turn.run_id} />}
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

/* Which tools ran, with what arguments, and what came back.

   Loaded only when asked for: a transcript is large and most answers are
   read without ever opening one. */
function Working({ runId }) {
  const { data, loading, error } = useQuery(
    `chat-run:${runId}`, () => api.agentRun(runId, { transcript: true }));

  if (loading) return <Loading message="Reading the working…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  // Unverified figures are NOT read from here. `/api/agents/runs/{id}`
  // does not carry them - they live on the answer itself, as
  // `unverified_figures`, which AgentAnswerView already renders above the
  // headline where they belong. Reading them here would have shown nothing
  // and hidden the warning behind a button nobody presses.
  const steps = data?.transcript || [];
  return (
    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
      {!steps.length && (
        <span className="tiny muted">No tool calls were recorded for this answer.</span>
      )}
      {steps.map((s, i) => (
        <div key={i} style={{
          fontSize: 11.5, padding: '7px 9px',
          background: 'var(--surface-2)', borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-subtle)',
        }}>
          {/* `index` is 1-based already, and 0 is the opening facts -
              fetched for the agent, not a step it chose. The "from step N"
              badges below carry the same numbering, so adding one here
              would make a repeat of step 2 point at a row labelled 3. */}
          <div className="tiny muted">
            {s.index ? `STEP ${s.index}` : 'OPENING FACTS'}
          </div>
          {s.thought && <div className="text-2" style={{ marginTop: 2 }}>{s.thought}</div>}
          {(s.calls || []).map((c, ci) => (
            <div key={ci} className="flex items-baseline gap-2 flex-wrap" style={{ marginTop: 4 }}>
              <Chip size="sm">{c.tool}</Chip>
              {c.args && Object.keys(c.args).length > 0 && (
                <code className="tiny text-3" style={{ overflowWrap: 'anywhere' }}>
                  {JSON.stringify(c.args)}
                </code>
              )}
              {c.repeat_of_step && (
                <Badge size="sm">from step {c.repeat_of_step}</Badge>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
