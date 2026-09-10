import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { titleCase } from '../core/format';
import { Button, Callout, Card, GlassCard, Chip, Loading, Search, Section, Select, Table, Badge } from '../ui';
import { Icon } from '../ui/icons';

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
  if (loading) return <Loading label="Compiling deterministic heuristic rulebook…" />;

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
        {section === 'model' && <ModelRules data={data} q={q} />}
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
    let items = data?.find?.institutions || [];
    if (q) items = items.filter((inst) => inst.name?.toLowerCase().includes(q) || inst.aliases?.some((a) => a.toLowerCase().includes(q)));
    return items;
  }, [data, q]);

  return (
    <Card title="Recognized Financial Institutions" subtitle={`${list.length} banking, card, and depository entities supported`} pad={false}>
      <div style={{ maxHeight: 540, overflow: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Institution</th>
              <th>Supported Classes</th>
              <th>Known Aliases / Sender Patterns</th>
            </tr>
          </thead>
          <tbody>
            {list.map((inst, i) => (
              <tr key={i} className="terminal-row">
                <td className="font-semibold">{inst.name}</td>
                <td>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {(inst.types || []).map((t) => <Chip size="sm" key={t}>{titleCase(t)}</Chip>)}
                  </div>
                </td>
                <td>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {(inst.aliases || []).map((a, j) => <span key={j} className="tiny num muted">{a}</span>)}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function EmailFilters({ data }) {
  const filters = data?.find?.email_queries || [];
  return (
    <Card title="Gmail Scan Matchers" subtitle="Search syntax queries applied during automatic mailbox synchronization" pad={false}>
      <div style={{ maxHeight: 500, overflow: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Provider / Document</th>
              <th>Search Filter Query</th>
            </tr>
          </thead>
          <tbody>
            {filters.map((f, i) => (
              <tr key={i} className="terminal-row">
                <td className="font-semibold">{f.label || f.name}</td>
                <td className="num tiny muted" style={{ fontFamily: 'var(--font-mono)' }}>{f.query}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Reading({ data }) {
  const readers = data?.read?.formats || [];
  return (
    <Card title="Document Readers & Coordinate Parsers" subtitle="Supported statement layout templates" pad={false}>
      <div style={{ maxHeight: 500, overflow: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Format ID</th>
              <th>Provider</th>
              <th>Reader Engine</th>
            </tr>
          </thead>
          <tbody>
            {readers.map((r, i) => (
              <tr key={i} className="terminal-row">
                <td className="font-semibold">{r.id}</td>
                <td>{r.institution || r.provider}</td>
                <td><Badge size="sm">{r.engine || 'Table Parser'}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Categories({ data, q }) {
  const categories = useMemo(() => {
    let cats = data?.check?.categories || [];
    if (q) cats = cats.filter((c) => c.name?.toLowerCase().includes(q) || c.pattern?.toLowerCase().includes(q));
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
              <th>Matching Terms & Keywords</th>
            </tr>
          </thead>
          <tbody>
            {categories.map((c, i) => (
              <tr key={i} className="terminal-row">
                <td className="num tiny muted">#{c.order ?? i + 1}</td>
                <td className="font-semibold nowrap"><Chip size="sm">{titleCase(c.name)}</Chip></td>
                <td><Terms pattern={c.pattern} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function LedgerRules({ data }) {
  const roles = data?.ledger?.flow_roles || [];
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
            {roles.map((r, i) => (
              <tr key={i} className="terminal-row">
                <td className="font-semibold nowrap">{r.role}</td>
                <td>{r.description}</td>
                <td><Badge tone={r.impact === 'none' ? 'brand' : r.impact === 'pos' ? 'pos' : 'neg'} size="sm">{r.impact}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Numbers({ data }) {
  const thresholds = data?.thresholds || [];
  return (
    <Card title="Tolerances & Standard Deviations" subtitle="Deterministic thresholds governing anomaly detection and matching" pad={false}>
      <div style={{ maxHeight: 500, overflow: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Parameter</th>
              <th>Value</th>
              <th>Analytical Purpose</th>
            </tr>
          </thead>
          <tbody>
            {thresholds.map((t, i) => (
              <tr key={i} className="terminal-row">
                <td className="font-semibold">{t.label || t.key}</td>
                <td className="num font-bold brand">{t.value}</td>
                <td className="small muted">{t.reason || t.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ModelRules() {
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
    </GlassCard>
  );
}
