import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { titleCase } from '../core/format';
import { Button, Callout, Card, GlassCard, Chip, Loading, Search, Select, Badge } from '../ui';

const SECTIONS = [
  ['explain', 'Explain Sandbox', 'Interactive regex & heuristic tester: paste any statement narration or sender.'],
  ['institutions', 'Institutions', 'Banks, credit card issuers, brokers, and depositories recognized by the system.'],
  ['email', 'Email Scanners', 'Gmail query filters, subject matchers, and spam refusal rules.'],
  ['reading', 'Document Parsers', 'Table extraction heuristics, coordinate readers, and regex layouts.'],
  ['categories', 'Classification Rules', 'Ordered heuristic dictionary determining transaction category.'],
  ['ledger', 'Accounting Invariants', 'Salary payday shifts, internal transfer netting, and zero double-counting.'],
  ['numbers', 'Tolerances & Limits', 'Every mathematical threshold, cluster window, and standard deviation limit.'],
  ['model', 'AI Model Invariants', 'Why no language model is ever permitted to compute an arithmetic figure.'],
];

function terms(pattern) {
  let source = String(pattern || '');
  for (let i = 0; i < 4; i += 1) {
    const next = source
      .replace(/\(\?:([^()|]*)\)\?/g, '$1?')
      .replace(/\(\?:([^()]*)\)/g, (_, inner) => inner.split('|').join('/'));
    if (next === source) break;
    source = next;
  }

  source = source
    .replace(/\.\{\d+,\d+\}\??|\.\*\??|\.\+\??/g, ' … ')
    .replace(/\[\^?[^\]]*\]\{?[\d,]*\}?\??/g, ' ')
    .replace(/\\d\{[\d,]+\}/g, '#')
    .replace(/\(\?[=!<][^)]*\)/g, ' ');

  const cleaned = source
    .split('|')
    .map((part) => part
      .replace(/\\b|\\B/g, ' ')
      .replace(/\\s[*+?]?/g, ' ')
      .replace(/\\d/g, '#')
      .replace(/\\\./g, '.')
      .replace(/[()?*+^$]/g, '')
      .replace(/\\/g, '')
      .replace(/\s+/g, ' ')
      .trim())
    .filter((t) => t && t.length > 1 && t !== '…');

  return [...new Set(cleaned)];
}

function Terms({ pattern, max = 12 }) {
  const list = terms(pattern);
  const shown = list.slice(0, max);
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }} title={pattern}>
      {shown.map((t, i) => <Chip size="sm" key={`${t}-${i}`}>{t}</Chip>)}
      {list.length > max && <span className="tiny muted">+{list.length - max} more</span>}
    </div>
  );
}

export default function Rules() {
  const [section, setSection] = useRouteParam('section', 'explain');
  const [search, setSearch] = useState('');
  const { data, loading, error } = useQuery('rules', () => api.rules());

  const active = SECTIONS.find(([k]) => k === section) || SECTIONS[0];
  const q = search.trim().toLowerCase();

  if (error) return <Callout tone="neg">Could not load rules dictionary: {error.message}</Callout>;
  if (loading) return <Loading message="Compiling deterministic heuristic rulebook…" />;

  return (
    <div className="rules-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Deterministic Rule Engine</h1>
            <Badge tone="brand" size="sm">Audited Source</Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            {active[2]}
          </p>
        </div>

        {section !== 'explain' && (
          <Search
            value={search}
            onChange={setSearch}
            placeholder="Search rule dictionary…"
            style={{ width: 240 }}
          />
        )}
      </div>

      {/* Section Nav Pills */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
        {SECTIONS.map(([key, label, hint]) => (
          <button
            key={key}
            type="button"
            className={`btn btn-sm ${section === key ? 'btn-primary' : 'btn-ghost'}`}
            title={hint}
            onClick={() => setSection(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Sub-view Viewport */}
      <div>
        {section === 'explain' && <Explain />}
        {section === 'institutions' && <Institutions data={data} q={q} />}
        {section === 'email' && <EmailFilters data={data} q={q} />}
        {section === 'reading' && <Reading data={data} q={q} />}
        {section === 'categories' && <Categories data={data} q={q} />}
        {section === 'ledger' && <LedgerRules data={data} q={q} />}
        {section === 'numbers' && <Numbers data={data} q={q} />}
        {section === 'model' && <ModelRules data={data} />}
      </div>
    </div>
  );
}

function Explain() {
  const [form, setForm] = useState({
    description: '',
    sender: '',
    subject: '',
    filename: '',
    direction: 'debit',
  });
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const anything = Object.entries(form).some(([k, v]) => k !== 'direction' && v.trim());

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.testRules(form));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const d = result?.description;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      <Callout tone="info">
        Zero database modifications. This runs the real production classification pipeline in sandbox mode
        to explain exactly which regex pattern or heuristic rule matches your input.
      </Callout>

      <GlassCard glowing style={{ padding: 'var(--space-5)' }}>
        <h3 className="h3" style={{ margin: 0 }}>Transaction Narration Sandbox</h3>
        <p className="tiny muted" style={{ margin: '4px 0 var(--space-4) 0' }}>
          Paste a bank or card line narration exactly as printed on your statement:
        </p>

        <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
          <input
            className="input"
            type="text"
            style={{ flex: 1, minWidth: 280 }}
            placeholder="e.g. UPI/SWIGGY/AUG25/123456/PAYMENT or POS 4012xxxx NETFLIX COM"
            value={form.description}
            onChange={set('description')}
            onKeyDown={(e) => e.key === 'Enter' && anything && run()}
          />
          <Select
            value={form.direction}
            onChange={(v) => setForm((f) => ({ ...f, direction: v }))}
            options={[['debit', 'Debit (Money Out)'], ['credit', 'Credit (Money In)']]}
          />
          <Button variant="primary" busy={busy} disabled={!anything} onClick={run}>
            Test Classification
          </Button>
        </div>
      </GlassCard>

      {error && <Callout tone="neg">{error}</Callout>}

      {d && (
        <Card title="Pipeline Execution Trace" subtitle="Step-by-step breakdown of how the engine processed the narration">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            <div>
              <div className="small font-semibold muted">1. Stripped & Normalized Token:</div>
              <div className="num font-semibold" style={{ marginTop: 4, padding: '8px 12px', background: 'var(--surface-3)', borderRadius: 'var(--radius-md)' }}>
                {d.normalized || <em>Empty after rail cleaning</em>}
              </div>
            </div>

            {d.winner ? (
              <div>
                <div className="small font-semibold muted">2. Victorious Classification Rule:</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
                  <Badge tone="pos" size="md">{titleCase(d.winner.category)}</Badge>
                  <span className="tiny muted">{Math.round(d.winner.confidence * 100)}% match confidence</span>
                  <span className="tiny muted">· Rule #{d.winner.order} in priority chain</span>
                </div>
                <div style={{ marginTop: 8 }}>
                  <Terms pattern={d.winner.pattern} />
                </div>
              </div>
            ) : (
              <Callout tone="warn">
                No deterministic regex rule matched. The item would be flagged for human triage in Review.
              </Callout>
            )}

            {d.also_matched?.length > 0 && (
              <div>
                <div className="small font-semibold muted">Shadowed Candidate Rules (Lost on precedence):</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
                  {d.also_matched.map((r, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }} className="tiny muted">
                      <span className="font-semibold">#{r.order} {titleCase(r.category)}</span>
                      <Terms pattern={r.pattern} max={6} />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}

function Institutions({ data, q }) {
  const list = useMemo(() => {
    const items = data?.find?.institutions || [];
    if (!q) return items;
    return items.filter((inst) => inst.name?.toLowerCase().includes(q)
      || (inst.match || []).some((a) => String(a).toLowerCase().includes(q))
      || String(inst.kind || '').toLowerCase().includes(q));
  }, [data, q]);

  return (
    <Card
      title="Recognized Financial Institutions"
      subtitle={`${list.length} banking, card, and depository entities supported`}
      pad={false}
    >
      <div className="table-wrapper" style={{ maxHeight: 540, overflowY: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Institution</th>
              <th>Class</th>
              <th>Sends</th>
              <th>Sender / filename patterns matched</th>
              <th>PDF password format</th>
            </tr>
          </thead>
          <tbody>
            {list.map((inst, i) => (
              <tr key={i} className="terminal-row">
                <td className="font-semibold nowrap">{inst.name}</td>
                <td className="nowrap"><Chip size="sm">{titleCase(inst.kind)}</Chip></td>
                <td>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {(inst.sends || []).map((t) => <Chip size="sm" key={t}>{titleCase(t)}</Chip>)}
                  </div>
                </td>
                <td>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {(inst.match || []).map((a, j) => (
                      <span key={j} className="tiny num muted">{a}</span>
                    ))}
                  </div>
                </td>
                <td className="tiny muted">
                  {inst.password || '—'}
                  {inst.password_note && <div className="tiny muted">({inst.password_note})</div>}
                </td>
              </tr>
            ))}
            {!list.length && <EmptyRow cols={5} />}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function EmptyRow({ cols, text = 'Nothing matches this search.' }) {
  return (
    <tr>
      <td colSpan={cols} className="muted tiny" style={{ padding: 'var(--space-5)', textAlign: 'center' }}>
        {text}
      </td>
    </tr>
  );
}

function EmailFilters({ data, q }) {
  const scans = useMemo(() => {
    const items = data?.find?.scans || [];
    if (!q) return items;
    return items.filter((f) => `${f.label} ${f.description} ${(f.subjects || []).join(' ')}`
      .toLowerCase().includes(q));
  }, [data, q]);
  const rejections = data?.find?.rejections || [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      <Card
        title="Gmail Scan Intents"
        subtitle="What each mailbox sweep looks for, and the subject lines that qualify"
        pad={false}
      >
        <div className="table-wrapper" style={{ maxHeight: 460, overflowY: 'auto' }}>
          <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th>Intent</th>
                <th>Attachment</th>
                <th>Look-back</th>
                <th>Subject phrases matched</th>
              </tr>
            </thead>
            <tbody>
              {scans.map((f, i) => (
                <tr key={i} className="terminal-row">
                  <td style={{ minWidth: 200 }}>
                    <div className="font-semibold">{f.label}</div>
                    <div className="tiny muted" style={{ whiteSpace: 'normal', maxWidth: 380 }}>
                      {f.description}
                    </div>
                  </td>
                  <td className="nowrap">
                    <Badge tone={f.needs_attachment ? 'brand' : ''} size="sm">
                      {f.needs_attachment ? 'Required' : 'Body only'}
                    </Badge>
                  </td>
                  <td className="num tiny muted nowrap">
                    {f.max_months ? `${f.max_months} months` : 'No limit'}
                  </td>
                  <td>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {(f.subjects || []).slice(0, 10).map((sub, j) => (
                        <Chip size="sm" key={j}>{sub}</Chip>
                      ))}
                      {(f.subjects || []).length > 10 && (
                        <span className="tiny muted">+{f.subjects.length - 10} more</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {!scans.length && <EmptyRow cols={4} />}
            </tbody>
          </table>
        </div>
      </Card>

      {rejections.length > 0 && (
        <Card title="Refusal Rules" subtitle="Mail deliberately skipped so marketing never reaches the ledger" pad={false}>
          <div className="table-wrapper">
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>Reason for refusal</th>
                  <th>Matching terms</th>
                </tr>
              </thead>
              <tbody>
                {rejections.map((r, i) => (
                  <tr key={i} className="terminal-row">
                    <td className="font-semibold nowrap">{r.reason}</td>
                    <td><Terms pattern={r.pattern} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function Reading({ data, q }) {
  const layouts = useMemo(() => {
    const items = data?.read?.portfolio_layouts || [];
    if (!q) return items;
    return items.filter((r) => `${r.layout} ${r.provider}`.toLowerCase().includes(q));
  }, [data, q]);

  const accountTypes = useMemo(() => {
    const items = data?.read?.account_types || [];
    if (!q) return items;
    return items.filter((r) => String(r.type).toLowerCase().includes(q));
  }, [data, q]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      <Card
        title="Account Type Detection"
        subtitle="How a parsed document is classified into a bank, card, loan or holdings account"
        pad={false}
      >
        <div className="table-wrapper" style={{ maxHeight: 320, overflowY: 'auto' }}>
          <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th>Resulting Account Type</th>
                <th>Terms matched in the document</th>
              </tr>
            </thead>
            <tbody>
              {accountTypes.map((r, i) => (
                <tr key={i} className="terminal-row">
                  <td className="font-semibold nowrap"><Chip size="sm">{titleCase(r.type)}</Chip></td>
                  <td><Terms pattern={r.pattern} /></td>
                </tr>
              ))}
              {!accountTypes.length && <EmptyRow cols={2} />}
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        title="Holdings Statement Layouts"
        subtitle="Depository and fund-house templates recognised by the portfolio reader"
        pad={false}
      >
        <div className="table-wrapper" style={{ maxHeight: 420, overflowY: 'auto' }}>
          <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th>Layout</th>
                <th>Provider</th>
                <th>Identifying phrases</th>
              </tr>
            </thead>
            <tbody>
              {layouts.map((r, i) => (
                <tr key={i} className="terminal-row">
                  <td className="font-semibold nowrap">{r.layout}</td>
                  <td className="nowrap">{r.provider || '—'}</td>
                  <td>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {(r.phrases || r.match || []).slice(0, 8).map((phrase, j) => (
                        <Chip size="sm" key={j}>{phrase}</Chip>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
              {!layouts.length && <EmptyRow cols={3} />}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function Categories({ data, q }) {
  const categories = useMemo(() => {
    let cats = data?.check?.categories || [];
    if (q) {
      cats = cats.filter((c) => String(c.category || '').toLowerCase().includes(q)
        || String(c.group || '').toLowerCase().includes(q)
        || String(c.pattern || '').toLowerCase().includes(q));
    }
    return cats;
  }, [data, q]);

  return (
    <Card title="Categorization Heuristics" subtitle="Deterministic regex patterns tried in strict priority order" pad={false}>
      <div style={{ maxHeight: 560, overflow: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Priority</th>
              <th>Category</th>
              <th>Group</th>
              <th>Applies to</th>
              <th style={{ textAlign: 'right' }}>Confidence</th>
              <th>Matching Terms &amp; Keywords</th>
            </tr>
          </thead>
          <tbody>
            {categories.map((c, i) => (
              <tr key={i} className="terminal-row">
                <td className="num tiny muted">#{c.order ?? i + 1}</td>
                <td className="font-semibold nowrap"><Chip size="sm">{titleCase(c.category)}</Chip></td>
                <td className="tiny muted nowrap">{c.group || '—'}</td>
                <td className="tiny muted nowrap">
                  {c.direction ? titleCase(c.direction) : 'Any direction'}
                </td>
                <td className="num tiny nowrap" style={{ textAlign: 'right' }}>
                  {c.confidence != null ? `${Math.round(c.confidence * 100)}%` : '—'}
                </td>
                <td>
                  <Terms pattern={c.pattern} />
                  {c.excludes && (
                    <div style={{ marginTop: 4 }}>
                      <span className="tiny muted">except: </span>
                      <Terms pattern={c.excludes} max={6} />
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {!categories.length && <EmptyRow cols={6} />}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function LedgerRules({ data, q }) {
  const all = data?.ledger?.flow_roles || [];
  const roles = q
    ? all.filter((r) => `${r.role} ${r.note} ${r.counts_as}`.toLowerCase().includes(q))
    : all;
  return (
    <Card title="Accounting Flow Roles" subtitle="Double-entry classifications guaranteeing mathematical tie-out" pad={false}>
      <div style={{ maxHeight: 500, overflow: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Flow Role</th>
              <th>Definition</th>
              <th>Impact on Spending / Income Totals</th>
            </tr>
          </thead>
          <tbody>
            {roles.map((r, i) => {
              const counts = String(r.counts_as || '');
              const tone = counts.startsWith('income') ? 'pos'
                : counts.startsWith('spending') ? 'neg' : 'brand';
              return (
                <tr key={i} className="terminal-row">
                  <td className="font-semibold nowrap">{titleCase(r.role)}</td>
                  <td style={{ whiteSpace: 'normal' }}>{r.note}</td>
                  <td><Badge tone={tone} size="sm">{counts || 'neither'}</Badge></td>
                </tr>
              );
            })}
            {!roles.length && <EmptyRow cols={3} />}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Numbers({ data, q }) {
  const all = data?.thresholds || [];
  const thresholds = q
    ? all.filter((t) => `${t.name} ${t.group} ${t.why} ${t.source}`.toLowerCase().includes(q))
    : all;
  return (
    <Card title="Tolerances & Standard Deviations" subtitle="Deterministic thresholds governing anomaly detection and matching" pad={false}>
      <div style={{ maxHeight: 500, overflow: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Area</th>
              <th>Parameter</th>
              <th style={{ textAlign: 'right' }}>Value</th>
              <th>Analytical Purpose</th>
              <th>Defined in</th>
            </tr>
          </thead>
          <tbody>
            {thresholds.map((t, i) => (
              <tr key={i} className="terminal-row">
                <td className="nowrap"><Chip size="sm">{t.group}</Chip></td>
                <td className="font-semibold" style={{ whiteSpace: 'normal' }}>{t.name}</td>
                <td className="num font-bold brand nowrap" style={{ textAlign: 'right' }}>
                  {t.value}{t.unit ? ` ${t.unit}` : ''}
                </td>
                <td className="small muted" style={{ whiteSpace: 'normal' }}>{t.why || '—'}</td>
                <td className="tiny muted nowrap" style={{ fontFamily: 'var(--font-mono)' }}>{t.source}</td>
              </tr>
            ))}
            {!thresholds.length && <EmptyRow cols={5} />}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ModelRules({ data }) {
  const model = data?.model || {};
  return (
    <GlassCard glowing style={{ padding: 'var(--space-6)' }}>
      <h3 className="h3" style={{ margin: 0 }}>Core System Invariant: Deterministic Arithmetic</h3>
      <div style={{ marginTop: 'var(--space-3)', display: 'flex', flexDirection: 'column', gap: 'var(--space-3)', lineHeight: 1.7 }} className="small muted">
        <p>
          <strong>No language model is ever permitted to compute a financial figure.</strong>
        </p>
        <p>
          In conventional AI applications, LLMs hallucinate numbers, miscalculate sums, and drop ledger rows.
          In this architecture, every single rupee figure, balance delta, and interest projection is computed by
          PostgreSQL / SQLite and audited pandas routines to the exact paise.
        </p>
        <p>
          The AI Copilot and agent models are strictly restricted to narrative synthesis, semantic category proposals,
          and natural language queries over verified database query results. Every number presented carries an immutable
          trace back to document line items.
        </p>
      </div>

      <div className="grid-2" style={{ marginTop: 'var(--space-5)', gap: 'var(--space-5)' }}>
        <div>
          <div className="small font-semibold" style={{ marginBottom: 6 }}>
            A model may be asked to
          </div>
          <ul style={{ margin: 0, paddingLeft: 18 }} className="small muted">
            {(model.used_for || []).map((line, i) => <li key={i}>{line}</li>)}
          </ul>
        </div>
        <div>
          <div className="small font-semibold" style={{ marginBottom: 6 }}>
            A model is never asked to
          </div>
          <ul style={{ margin: 0, paddingLeft: 18 }} className="small muted">
            {(model.never_used_for || []).map((line, i) => <li key={i}>{line}</li>)}
          </ul>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 'var(--space-4)' }}>
        <Badge tone={model.enabled ? 'pos' : ''} size="sm">
          {model.enabled ? 'Model inference enabled' : 'Model inference disabled'}
        </Badge>
        {model.batch_size != null && <Badge size="sm">Batch size {model.batch_size}</Badge>}
      </div>
    </GlassCard>
  );
}
