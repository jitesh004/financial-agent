/* ────────────────────────────────────────────────────────────────────────────
   Structured agent answer renderer.

   `/api/agents/runs/{id}` returns `answer` as an object — headline, summary,
   metrics, findings, actions, caveats — so it can never be dropped straight
   into JSX. Both the Agents hub and the Copilot drawer render it through here.
   ──────────────────────────────────────────────────────────────────────── */

import React from 'react';
import { Badge, Chip, Icon } from './index';

const SEVERITY_TONE = {
  critical: 'neg', high: 'neg', alert: 'neg',
  watch: 'warn', medium: 'warn', caution: 'warn',
  info: '', low: '', good: 'pos', ok: 'pos',
};

const EFFORT_TONE = { low: 'pos', medium: 'warn', high: 'neg' };

export function AgentAnswerView({ answer, compact = false }) {
  if (!answer) return null;

  /* Older runs stored a plain string; newer ones a structured object. */
  if (typeof answer === 'string') {
    return (
      <div style={{ fontSize: 13.5, lineHeight: 1.65, whiteSpace: 'pre-wrap' }}>{answer}</div>
    );
  }

  const { headline, summary, metrics = [], findings = [], actions = [],
          caveats = [], unverified_figures: unverified = [] } = answer;

  return (
    <div className="flex-col gap-4">
      {/* Above the headline, deliberately. The figure check already catches
          numbers the model produced rather than read - it named all three
          on a run that reported a debt this holder does not have - but it
          said so in muted text at the bottom while the invented figure led
          in large type at the top. A correction nobody reads first is not
          a correction. */}
      {/* `--neg`, not `--danger`. There is no `--danger` in this design
          system and there never was, so every rule above resolved to
          nothing: the border fell back to currentColor and the heading kept
          body text colour. The one banner in the app whose entire job is to
          look alarming was rendering as ordinary prose in a faint box. */}
      {unverified.length > 0 && (
        <div style={{
          border: '1px solid var(--neg-border)', borderLeft: '3px solid var(--neg)',
          borderRadius: 4, padding: '8px 12px', background: 'var(--neg-soft)',
        }}>
          <div className="small font-semibold" style={{ color: 'var(--neg)' }}>
            {unverified.length} figure{unverified.length > 1 ? 's' : ''} in this
            answer did not come from your ledger
          </div>
          <div className="tiny muted" style={{ marginTop: 2 }}>
            {unverified.slice(0, 6).join(', ')} — the model produced
            {unverified.length > 1 ? ' these' : ' this'} rather than reading
            {unverified.length > 1 ? ' them' : ' it'}. Check against the
            working before relying on anything here.
          </div>
        </div>
      )}

      {headline && (
        <h3 style={{
          fontSize: compact ? 15 : 19, fontWeight: 800, lineHeight: 1.35, margin: 0,
        }}>
          {headline}
        </h3>
      )}

      {summary && (
        <p style={{ fontSize: 13.5, lineHeight: 1.65, color: 'var(--text-2)', margin: 0 }}>
          {summary}
        </p>
      )}

      {metrics.length > 0 && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: `repeat(auto-fit, minmax(${compact ? 150 : 190}px, 1fr))`,
          gap: 10,
        }}>
          {metrics.map((m, i) => (
            <div
              key={i}
              style={{
                padding: '10px 12px', borderRadius: 'var(--r-sm)',
                background: 'var(--surface-2)', border: '1px solid var(--line)',
              }}
              title={m.note || ''}
            >
              <div className="drill-stat-label">{m.label}</div>
              <div className="tabular-nums" style={{ fontSize: 16, fontWeight: 800, marginTop: 2 }}>
                {m.unit === 'INR' ? '₹' : ''}{m.value}
                {m.unit && m.unit !== 'INR' && (
                  <span className="tiny muted" style={{ marginLeft: 4 }}>{m.unit}</span>
                )}
              </div>
              {m.note && !compact && (
                <div className="tiny muted" style={{ marginTop: 4, whiteSpace: 'normal' }}>{m.note}</div>
              )}
            </div>
          ))}
        </div>
      )}

      {findings.length > 0 && (
        <div className="flex-col gap-2">
          <div className="drill-stat-label">What the agent found</div>
          {findings.map((f, i) => (
            <div
              key={i}
              style={{
                padding: '10px 12px', borderRadius: 'var(--r-sm)',
                background: 'var(--surface-2)', border: '1px solid var(--line)',
              }}
            >
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <strong style={{ fontSize: 13.5 }}>{f.title}</strong>
                {f.severity && (
                  <Badge tone={SEVERITY_TONE[String(f.severity).toLowerCase()] ?? 'warn'} size="sm">
                    {f.severity}
                  </Badge>
                )}
              </div>
              {f.detail && (
                <div className="small" style={{ color: 'var(--text-2)', marginTop: 4, lineHeight: 1.55 }}>
                  {f.detail}
                </div>
              )}
              {f.evidence?.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>
                  {f.evidence.map((e, j) => (
                    <Chip key={j} size="sm">{e}</Chip>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {actions.length > 0 && (
        <div className="flex-col gap-2">
          <div className="drill-stat-label">What to do about it</div>
          {actions.map((a, i) => (
            <div
              key={i}
              style={{
                padding: '10px 12px', borderRadius: 'var(--r-sm)',
                background: 'var(--accent-soft)', border: '1px solid var(--accent-border)',
              }}
            >
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <strong style={{ fontSize: 13.5 }}>{a.title}</strong>
                {a.effort && (
                  <Badge tone={EFFORT_TONE[String(a.effort).toLowerCase()] ?? ''} size="sm">
                    {a.effort} effort
                  </Badge>
                )}
              </div>
              {a.detail && (
                <div className="small" style={{ color: 'var(--text-2)', marginTop: 4, lineHeight: 1.55 }}>
                  {a.detail}
                </div>
              )}
              {a.mechanism && (
                <div className="tiny muted" style={{ marginTop: 6, lineHeight: 1.5 }}>
                  <Icon name="info" size={11} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                  {a.mechanism}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {caveats.length > 0 && (
        <div>
          <div className="drill-stat-label">Caveats</div>
          <ul className="small muted" style={{ margin: '6px 0 0', paddingLeft: 18, lineHeight: 1.6 }}>
            {caveats.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

/* Run-to-run movement, when the server computed one. */
export function AgentRunDiff({ diff }) {
  if (!diff?.available) return null;
  const moved = diff.metrics_moved || [];
  const added = diff.new_findings || [];
  const resolved = diff.resolved_findings || [];
  if (!moved.length && !added.length && !resolved.length) return null;

  return (
    <div style={{
      padding: '10px 12px', borderRadius: 'var(--r-sm)',
      background: 'var(--surface-2)', border: '1px solid var(--line)',
    }}>
      <div className="drill-stat-label">Since the previous run</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
        {moved.map((m, i) => (
          <Chip key={`m${i}`} tone="brand" size="sm">
            {typeof m === 'string' ? m : `${m.label}: ${m.from} → ${m.to}`}
          </Chip>
        ))}
        {added.map((f, i) => <Chip key={`a${i}`} tone="warn" size="sm">new: {f}</Chip>)}
        {resolved.map((f, i) => <Chip key={`r${i}`} tone="pos" size="sm">resolved: {f}</Chip>)}
      </div>
    </div>
  );
}

export default AgentAnswerView;
