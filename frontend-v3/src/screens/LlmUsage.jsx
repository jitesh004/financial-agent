/* ────────────────────────────────────────────────────────────────────────────
   What the language model has been asked, and what it cost.

   One tab per purpose plus an overall tab, a window that goes down to
   minutes, and a filter by key. Clicking a request count opens the calls
   behind it - prompt, reply, tokens, latency, which key, and any attempt
   that failed with the retry that followed it.

   Two rules run through the whole screen:

     - A figure and the list you get by clicking it must describe the same
       set. Both come from one window parser on the server for that reason.
     - An unknown cost is said, never rendered as zero. "Not priced" and
       "free" are different facts and this page is the one place that
       difference matters.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { count } from '../core/format';
import {
  Badge, Button, Callout, Card, Chip, Empty, Loading, Select, Stat,
} from '../ui';
import { Icon } from '../ui/icons';

/* Windows a person actually asks for. Minutes are here because a rate
   limit is a per-minute window, and "have I just burned my quota" is a
   question about the last few minutes rather than the last few days. */
const PERIODS = [
  ['15m', 'Last 15 min'], ['1h', 'Last hour'], ['6h', 'Last 6 hours'],
  ['24h', 'Last 24 hours'], ['7d', 'Last 7 days'], ['30d', 'Last 30 days'],
  ['3mo', 'Last 3 months'], ['12mo', 'Last 12 months'], ['all', 'All time'],
];

const STATUS_TONE = {
  ok: 'pos', rate_limited: 'warn', rejected: 'neg', failed: 'neg',
};

const STATUS_LABEL = {
  ok: 'ok', rate_limited: 'rate limited', rejected: 'key rejected',
  failed: 'failed',
};

const nf = (n) => count(Number(n) || 0);

/* Cost arrives in millionths of a US DOLLAR, and `cost_known` says whether
   a price for that model is published at all.

   Dollars, not rupees. Google prices in USD and this app totals a person's
   money in rupees, and converting between them at a rate nobody chose is
   the thing `pipeline.enrich` refuses to do everywhere else: "inventing an
   exchange rate would be a worse answer than saying the figure needs a
   human." So the unit is stated rather than translated.

   A model nobody has priced renders as "not priced" - a confident zero on
   the page whose whole job is reporting cost would be the one lie that
   matters here. Free tier IS zero, and says so differently. */
function Money({ micros, known }) {
  if (!known) return <span className="text-3">not priced</span>;
  const usd = (Number(micros) || 0) / 1_000_000;
  if (usd === 0) return <span style={{ color: 'var(--pos)' }}>$0.00</span>;
  // Four decimals below a cent: one categorisation batch costs a fraction
  // of a cent, and rounding to cents reports a day's work as zero.
  return <span className="num">${usd < 0.01 ? usd.toFixed(6) : usd.toFixed(4)}</span>;
}

export default function LlmUsage() {
  const [period, setPeriod] = useState('24h');
  const [keyFilter, setKeyFilter] = useState('');
  const [tab, setTab] = useState('overall');
  const [showCalls, setShowCalls] = useState(false);
  const [onlyFailed, setOnlyFailed] = useState(false);

  const q = `period=${period}&key=${encodeURIComponent(keyFilter)}`;
  const { data, loading, error, refetch } = useQuery(
    `llm-usage:${q}`, () => api.llmUsage(period, keyFilter),
  );

  const purposes = data?.purposes || [];
  const byPurpose = useMemo(
    () => Object.fromEntries((data?.by_purpose || []).map((p) => [p.purpose, p])),
    [data],
  );
  const overall = data?.overall || {};
  const active = tab === 'overall' ? overall : (byPurpose[tab] || {});
  const keys = data?.keys || [];

  if (loading && !data) return <Loading message="Reading model usage…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  return (
    <div className="page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      <div className="page-head">
        <div>
          <h1 className="h1" style={{ margin: 0 }}>Model Usage</h1>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Every request this workspace has made to a language model &mdash;
            what asked for it, what it cost, which key carried it, and what
            went wrong.
          </p>
        </div>
      </div>

      {/* ─────────────────────────────────────────────────────── filters */}
      <Card pad>
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="tiny muted">Period</span>
            <Select
              size="sm"
              value={period}
              onChange={setPeriod}
              options={PERIODS}
              style={{ minWidth: 150 }}
            />
          </div>

          <div className="flex items-center gap-2">
            <span className="tiny muted">API key</span>
            <Select
              size="sm"
              value={keyFilter}
              onChange={setKeyFilter}
              options={[['', 'All keys'],
                ...keys.map((k) => [k.key_label,
                  `${k.key_label || '(unlabelled)'} · ${nf(k.attempts)}`])]}
              style={{ minWidth: 190 }}
            />
          </div>

          <span style={{ flex: 1 }} />
          <span className="tiny muted">{data?.window?.label}</span>
          <Button size="xs" variant="ghost" onClick={refetch}>Refresh</Button>
        </div>
      </Card>

      {/* ─────────────────────────────────────────────────────────── tabs */}
      <div className="flex items-center gap-2 flex-wrap">
        <TabButton
          active={tab === 'overall'}
          onClick={() => { setTab('overall'); setShowCalls(false); }}
          label="Overall"
          n={overall.requests}
        />
        {/* Every purpose the app HAS, not only those with traffic. A tab
            reading zero says "categorisation has not run"; an absent tab
            says nothing at all. */}
        {purposes.map((p) => (
          <TabButton
            key={p.key}
            active={tab === p.key}
            onClick={() => { setTab(p.key); setShowCalls(false); }}
            label={p.label}
            n={byPurpose[p.key]?.requests}
          />
        ))}
      </div>

      {/* ──────────────────────────────────────────────────────── figures */}
      <div className="stats-grid">
        <Stat
          label="Requests"
          value={nf(active.requests)}
          sub={active.attempts > active.requests
            ? `${nf(active.attempts)} attempts — ${nf(active.attempts - active.requests)} were retries`
            : 'one attempt each'}
        />
        <Stat label="Input tokens" value={nf(active.input_tokens)} sub="sent to the model" />
        <Stat label="Output tokens" value={nf(active.output_tokens)}
          sub="returned, reasoning included" />
        <Stat
          label="Estimated cost"
          value={<Money micros={active.cost_micros}
            known={!Number(active.unpriced || 0)} />}
          sub={Number(active.unpriced || 0)
            ? `${nf(active.unpriced)} calls on a model with no price on record`
            : 'from the model’s published rate'}
        />
      </div>

      <div className="stats-grid">
        <Stat label="Failed attempts" value={nf(active.failed)}
          tone={Number(active.failed) ? 'neg' : undefined}
          sub={Number(active.failed) ? 'rate limits, rejections and errors' : 'none'} />
        <Stat label="Slowest call"
          value={`${((Number(active.slowest_ms) || 0) / 1000).toFixed(1)}s`} />
        <Stat label="Average call"
          value={`${((Number(active.average_ms) || 0) / 1000).toFixed(1)}s`} />
        <Stat label="Prompt volume" value={nf(active.prompt_chars)}
          sub="characters sent" />
      </div>

      {/* ─────────────────────────────────────── models and keys in play */}
      {tab === 'overall' && (
        <div className="grid-2" style={{ gap: 'var(--space-4)' }}>
          <Card pad title="Models used"
            sub="What actually ran, which is not the same as what is configured">
            {!(data?.models || []).length && (
              <span className="tiny muted">No calls in this window.</span>
            )}
            {(data?.models || []).map((m) => (
              <div key={`${m.provider}:${m.model}:${m.reasoning}`}
                className="flex items-center gap-2 flex-wrap"
                style={{ padding: '6px 0', borderTop: '1px solid var(--border-subtle)' }}>
                <Chip size="sm">{m.provider}</Chip>
                <strong style={{ fontSize: 13 }}>{m.model}</strong>
                <Badge size="sm"
                  tone={m.reasoning === 'off' ? undefined : 'brand'}
                  title="Whether reasoning was requested. A thinking model charges its reasoning against the same token budget.">
                  reasoning: {m.reasoning || 'unknown'}
                </Badge>
                <span style={{ flex: 1 }} />
                <span className="tiny muted">
                  {nf(m.attempts)} calls · {nf(m.total_tokens)} tokens
                </span>
              </div>
            ))}
          </Card>

          <Card pad title="Keys used"
            sub="Which credential carried the traffic, and what it cost them">
            {!keys.length && <span className="tiny muted">No calls in this window.</span>}
            {keys.map((k) => (
              <div key={k.key_label || k.key_hint}
                className="flex items-center gap-2 flex-wrap"
                style={{ padding: '6px 0', borderTop: '1px solid var(--border-subtle)' }}>
                <strong style={{ fontSize: 13 }}>{k.key_label || '(unlabelled)'}</strong>
                <code className="tiny text-3">{k.key_hint}</code>
                <span style={{ flex: 1 }} />
                {Number(k.failed) > 0 && (
                  <Badge size="sm" tone="neg">{nf(k.failed)} failed</Badge>
                )}
                <span className="tiny muted">{nf(k.attempts)} calls</span>
              </div>
            ))}
          </Card>
        </div>
      )}

      {/* ───────────────────────────────────────────────── the calls */}
      <Card pad
        title="Requests"
        sub={tab === 'overall'
          ? 'Every call in this window'
          : `Calls made for ${purposes.find((p) => p.key === tab)?.label || tab}`}
      >
        <div className="flex items-center gap-2 flex-wrap" style={{ marginBottom: 10 }}>
          <Button size="sm" variant={showCalls ? 'ghost' : 'secondary'}
            onClick={() => setShowCalls(!showCalls)}>
            {showCalls ? 'Hide' : `Show ${nf(active.requests)} requests`}
          </Button>
          {showCalls && (
            <Button size="xs" variant={onlyFailed ? 'primary' : 'ghost'}
              onClick={() => setOnlyFailed(!onlyFailed)}>
              {onlyFailed ? 'Showing failures only' : 'Failures only'}
            </Button>
          )}
        </div>

        {showCalls && (
          <CallList
            period={period}
            keyFilter={keyFilter}
            purpose={tab === 'overall' ? '' : tab}
            status={onlyFailed ? 'failed' : ''}
          />
        )}
      </Card>
    </div>
  );
}

function TabButton({ active, onClick, label, n }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '7px 14px',
        borderRadius: 'var(--r-sm)',
        border: `1px solid ${active ? 'var(--accent)' : 'var(--border-subtle)'}`,
        background: active ? 'var(--accent-soft)' : 'var(--surface)',
        color: active ? 'var(--accent-text)' : 'var(--text-2)',
        fontSize: 13,
        fontWeight: active ? 600 : 500,
        cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 7,
      }}
    >
      {label}
      <span className="tiny" style={{ opacity: 0.75 }}>{nf(n)}</span>
    </button>
  );
}

function CallList({ period, keyFilter, purpose, status }) {
  const key = `llm-calls:${period}:${keyFilter}:${purpose}:${status}`;
  const { data, loading, error } = useQuery(
    key, () => api.llmCalls({ period, key: keyFilter, purpose, status, limit: 100 }),
  );

  if (loading) return <Loading message="Reading the call log…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  const calls = data?.calls || [];
  if (!calls.length) {
    return (
      <Empty title="No calls in this window" icon="sparkles">
        Nothing asked a model here. That is the cheapest possible outcome.
      </Empty>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {calls.map((c) => <CallRow key={c.group_id} call={c} />)}
      {data.total > calls.length && (
        <span className="tiny muted">
          Showing the {calls.length} most recent of {nf(data.total)}.
        </span>
      )}
    </div>
  );
}

function CallRow({ call }) {
  const [open, setOpen] = useState(false);
  const when = (call.created_at || '').replace('T', ' ').slice(0, 19);
  const failedTries = (call.attempts || []).filter((a) => a.status !== 'ok');

  return (
    <div style={{
      border: `1px solid ${call.outcome === 'ok' ? 'var(--border-subtle)' : 'var(--neg-border)'}`,
      borderRadius: 'var(--radius-md)',
      background: 'var(--surface-2)',
      padding: '10px 12px',
    }}>
      <div className="flex items-center gap-2 flex-wrap">
        <Badge size="sm" tone={STATUS_TONE[call.outcome]}>
          {STATUS_LABEL[call.outcome] || call.outcome}
        </Badge>
        <strong style={{ fontSize: 13 }}>{call.subject || call.purpose}</strong>
        <Chip size="sm">{call.purpose}</Chip>
        <span style={{ flex: 1 }} />
        <span className="tiny muted" title={call.created_at}>{when}</span>
        <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Detail'}
        </Button>
      </div>

      <div className="tiny muted flex items-center gap-3 flex-wrap" style={{ marginTop: 5 }}>
        <span>{call.model}</span>
        <span>in {nf(call.input_tokens)} · out {nf(call.output_tokens)}</span>
        <span>{((Number(call.latency_ms) || 0) / 1000).toFixed(2)}s</span>
        <span>key: {call.key_label || '—'}</span>
        <span>reasoning: {call.reasoning || 'unknown'}</span>
        <Money micros={call.cost_micros} known={call.cost_known} />
        {/* A retry is shown against the request it retried, not as a
            separate lost call - the next row down is the same question
            answered on another key, and listing them apart reads as two
            failures. */}
        {call.retries > 0 && (
          <Badge size="sm" tone="warn">
            {call.retries} retr{call.retries === 1 ? 'y' : 'ies'}
          </Badge>
        )}
      </div>

      {call.error && !open && (
        <div className="tiny" style={{ color: 'var(--neg)', marginTop: 5 }}>
          {call.error}
        </div>
      )}

      {open && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {failedTries.length > 0 && (
            <div>
              <div className="tiny muted" style={{ marginBottom: 4, letterSpacing: '.04em' }}>
                ATTEMPTS
              </div>
              {(call.attempts || []).map((a) => (
                <div key={a.id} className="flex items-center gap-2 flex-wrap"
                  style={{ fontSize: 11.5, padding: '3px 0' }}>
                  <Badge size="sm" tone={STATUS_TONE[a.status]}>
                    #{a.attempt} {STATUS_LABEL[a.status] || a.status}
                  </Badge>
                  <span className="text-3">{a.key_label || '—'}</span>
                  {a.http_status > 0 && <code className="text-3">HTTP {a.http_status}</code>}
                  <span className="text-3">{((Number(a.latency_ms) || 0) / 1000).toFixed(2)}s</span>
                  {a.error && <span style={{ color: 'var(--neg)' }}>{a.error}</span>}
                </div>
              ))}
            </div>
          )}
          <Pane label="Sent" body={call.request_preview} chars={call.prompt_chars} />
          <Pane label="Returned" body={call.response_preview} chars={call.response_chars} />
        </div>
      )}
    </div>
  );
}

function Pane({ label, body, chars }) {
  return (
    <div>
      <div className="tiny muted flex items-center gap-2"
        style={{ marginBottom: 3, letterSpacing: '.04em' }}>
        <span>{label.toUpperCase()}</span>
        {chars > 0 && <span style={{ opacity: 0.7 }}>{nf(chars)} chars</span>}
        {chars > 4000 && (
          <span style={{ opacity: 0.7 }} title="Only the first 4,000 characters are kept.">
            <Icon name="info" size={11} /> truncated
          </span>
        )}
      </div>
      <pre style={{
        margin: 0, padding: '8px 10px', fontSize: 11.5, lineHeight: 1.5,
        background: 'var(--surface)', border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-sm)', maxHeight: 260, overflow: 'auto',
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {body || '—'}
      </pre>
    </div>
  );
}
