/* Every rule the app runs on your documents, readable.
 *
 * The app decides a great deal on your behalf - which emails are worth
 * downloading, which reader a PDF goes to, which way a row's money moved, what
 * a merchant is - and without this screen every one of those decisions is
 * invisible. A row saying "Dining · rule" with no way, anywhere, to learn
 * which rule or why is a bad position for software that tells somebody what
 * they spent.
 *
 * Read-only on purpose. These rules live in code, reviewed and tested; an
 * editable copy here would be a second source of truth for every one of them,
 * which is the exact fault the rules package was built to remove.
 *
 * "Explain" is first because a catalogue you have to read is a reference,
 * while a box you can paste a narration into is an answer.
 */

import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { titleCase } from '../core/format';
import { Button, Callout, Card, Chip, Loading, Search, Section, Select, Table } from '../ui';

const SECTIONS = [
  ['explain', 'Explain', 'Paste a narration or a sender and see exactly what fires.'],
  ['institutions', 'Institutions', 'Every bank, card, broker and bureau the app can recognise, and what it knows about each.'],
  ['email', 'Email filters', 'What each scan searches for, and every reason an email is refused.'],
  ['reading', 'Reading documents', 'How a statement, a bureau report and a holdings statement are taken apart.'],
  ['categories', 'Categories', 'The rules that decide what a transaction was for, in the order they are tried.'],
  ['ledger', 'Your ledger', 'What happens after the rows exist: which month a salary counts in, why two rows were paired, what counts as spending.'],
  ['numbers', 'Numbers', 'Every tolerance, window and limit, with the reason it is that number.'],
  ['vocabulary', 'Vocabulary', 'The shapes documents are written in: months, payment rails, blank figures.'],
  ['pipeline', 'How it runs', 'The order things happen in, how a format is detected, and which reader a document goes to.'],
  ['money', 'Forecast & loans', 'How next month is estimated, and why a loan is arithmetic rather than a guess.'],
  ['model', 'The model', 'What a language model is used for, what it is never used for, and what is stripped before it sees anything.'],
  ['storage', 'Your data', 'What each clearing action keeps, and what it can never bring back.'],
];

const KIND_TONE = {
  bank: 'acc', card: 'pos', loan: 'warn', bureau: 'neg', broker: '', wallet: '',
};

/* A regex is the honest answer to "what does this rule look for", but not a
   readable one. The alternation is the part a person can actually scan, so it
   is shown as terms with the syntax stripped. The full pattern stays one hover
   away rather than being hidden. */
function terms(pattern) {
  let source = String(pattern || '');
  // An inner alternation is one choice within a term, not a term of its own:
  // "(?:payment|pmt)" is one thing spelled two ways. Collapsed BEFORE the
  // split, or "pmt" ends up looking like a rule of its own. Repeated because
  // these groups nest.
  for (let i = 0; i < 4; i += 1) {
    const next = source
      .replace(/\(\?:([^()|]*)\)\?/g, '$1?')
      .replace(/\(\?:([^()]*)\)/g, (_, inner) => inner.split('|').join('/'));
    if (next === source) break;
    source = next;
  }

  source = source
    // A wildcard run is "anything in between", worth showing as a gap rather
    // than as punctuation.
    .replace(/\.\{\d+,\d+\}\??|\.\*\??|\.\+\??/g, ' … ')
    // Character classes are matching detail, not vocabulary.
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

  // Several alternatives often reduce to the same readable term - a salary
  // rule spells SAL three ways to survive Indian payroll narrations, and
  // showing "SAL" three times reads as a mistake rather than as thoroughness.
  return [...new Set(cleaned)];
}

function Terms({ pattern, max = 14 }) {
  const list = terms(pattern);
  const shown = list.slice(0, max);
  return (
    <span className="row tight" title={pattern}>
      {shown.map((t, i) => <Chip key={`${t}-${i}`}>{t}</Chip>)}
      {list.length > max && <span className="tiny dim">+{list.length - max} more</span>}
    </span>
  );
}

function Rows({ head, rows, cols }) {
  if (!rows.length) {
    return <div className="small dim">Nothing here matches your search.</div>;
  }
  return (
    <Table cols={cols}>
      <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>
        {rows.map((cells, i) => (
          <tr key={i}>{cells.map((c, j) => <td key={j}>{c}</td>)}</tr>
        ))}
      </tbody>
    </Table>
  );
}

function Chips({ list, match = () => true }) {
  return (
    <span className="row tight">
      {list.filter(match).map((x) => <Chip key={x}>{x}</Chip>)}
    </span>
  );
}

export default function Rules() {
  const [section, setSection] = useRouteParam('section', 'explain');
  const [search, setSearch] = useState('');
  const { data, loading, error } = useQuery('rules', () => api.rules());

  const active = SECTIONS.find(([k]) => k === section) || SECTIONS[0];
  const q = search.trim().toLowerCase();

  const counts = useMemo(() => (data ? {
    institutions: data.find.institutions.length,
    categories: data.check.categories.length,
    numbers: data.thresholds.length,
    ledger: (data.ledger?.flow_roles.length || 0) + (data.ledger?.directions.length || 0),
  } : {}), [data]);

  if (error) return <Callout tone="neg">Could not load the rules: {error.message}</Callout>;
  if (loading) return <Loading label="Reading the rules…" />;

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h2 className="h2">Rules</h2>
          <p className="lead">{active[2]}</p>
        </div>
        {section !== 'explain' && (
          <Search value={search} onChange={setSearch} placeholder="Search these rules"
            style={{ width: 220 }} />
        )}
      </div>

      <div className="row tight">
        {SECTIONS.map(([key, label, hint]) => (
          <button
            key={key}
            type="button"
            className={`tog ${section === key ? 'on' : ''}`}
            title={hint}
            onClick={() => setSection(key)}
          >
            {label}
            {counts[key] != null && <span className="tiny dim">{counts[key]}</span>}
          </button>
        ))}
      </div>

      <div className="col">
        {section === 'explain' && <Explain />}
        {section === 'institutions' && <Institutions data={data} q={q} />}
        {section === 'email' && <EmailFilters data={data} q={q} />}
        {section === 'reading' && <Reading data={data} q={q} />}
        {section === 'categories' && <Categories data={data} q={q} />}
        {section === 'ledger' && <LedgerRules data={data} q={q} />}
        {section === 'numbers' && <Numbers data={data} q={q} />}
        {section === 'vocabulary' && <Vocabulary data={data} q={q} />}
        {section === 'pipeline' && <Pipeline data={data} q={q} />}
        {section === 'money' && <Money data={data} q={q} />}
        {section === 'model' && <Model data={data} q={q} />}
        {section === 'storage' && <Storage data={data} q={q} />}
      </div>
    </>
  );
}

/* ─────────────────────────────────────────────────────────── explain ───── */

function Explain() {
  const [form, setForm] = useState({
    description: '', sender: '', subject: '', filename: '', direction: 'debit',
  });
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const anything = Object.entries(form).some(([k, v]) => k !== 'direction' && v.trim());

  const run = async () => {
    setBusy(true);
    setError(null);
    try { setResult(await api.testRules(form)); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const d = result?.description;
  const m = result?.email;

  return (
    <>
      <Callout tone="acc">
        Nothing here is stored and no total changes. This runs the same functions the
        import runs, so what it reports is what actually happens.
      </Callout>

      <Card title="A transaction" sub="Paste a narration exactly as it appears on the statement">
        <div className="row">
          <input
            type="text" style={{ flex: 1, minWidth: 260 }}
            placeholder="UPI/SWIGGY/AUG25/123456"
            value={form.description} onChange={set('description')}
            onKeyDown={(e) => e.key === 'Enter' && anything && run()}
          />
          <Select value={form.direction} onChange={(v) => setForm((f) => ({ ...f, direction: v }))}
            options={[['debit', 'Money out'], ['credit', 'Money in']]} />
        </div>
      </Card>

      <Card title="An email" sub="Or a sender, subject and filename, to see what a scan would do with it">
        <div className="col">
          <input type="text" placeholder="alerts@hdfcbank.net"
            value={form.sender} onChange={set('sender')} />
          <input type="text" placeholder="Your HDFC Bank credit card statement"
            value={form.subject} onChange={set('subject')} />
          <input type="text" placeholder="Retail_HPCL_NORM.pdf"
            value={form.filename} onChange={set('filename')} />
        </div>
      </Card>

      <div>
        <Button variant="primary" busy={busy} disabled={!anything} onClick={run}>
          Explain
        </Button>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}

      {d && (
        <Card title="The transaction" pad>
          <Section
            title="What the app reads"
            note="Rail prefixes and reference numbers are stripped before any rule sees it."
          />
          <div className="mono small" style={{ margin: '6px 0 16px' }}>
            {(d.searched?.length ? d.searched : [d.normalized]).map((line, i) => (
              <div key={i}>{line || <em>nothing left after cleaning</em>}</div>
            ))}
          </div>

          {d.winner ? (
            <>
              <Section
                title="The rule that decides it"
                note={`Rule ${d.winner.order} of the list. The first match wins, which is why order matters.`}
              />
              <div className="row tight" style={{ margin: '6px 0' }}>
                <Chip tone="pos">{titleCase(d.winner.category)}</Chip>
                <span className="tiny dim">
                  {Math.round(d.winner.confidence * 100)}% confident
                  {d.winner.direction
                    ? ` · money ${d.winner.direction === 'credit' ? 'in' : 'out'} only` : ''}
                </span>
              </div>
              <Terms pattern={d.winner.pattern} />
            </>
          ) : (
            <Callout tone="warn">
              No rule matches this. It would be left uncategorised, or sent to the model if
              you have that turned on.
            </Callout>
          )}

          {d.also_matched?.length > 0 && (
            <>
              <Section title="Also matched, but lost"
                note="These would have fired if the winner above were not listed first." />
              <Rows
                head={['Order', 'Category', 'Looks for']}
                rows={d.also_matched.map((r) => [
                  r.order, titleCase(r.category), <Terms pattern={r.pattern} max={8} />,
                ])}
              />
            </>
          )}

          {d.vetoed?.length > 0 && (
            <>
              <Section
                title="Matched, then stood down"
                note="These found what they look for and gave the row up anyway, because a second pattern rules them out."
              />
              <Rows
                head={['Order', 'Category', 'Looks for', 'Ruled out by']}
                rows={d.vetoed.map((r) => [
                  r.order, titleCase(r.category), <Terms pattern={r.pattern} max={6} />,
                  <Chip tone="warn">{r.vetoed_by}</Chip>,
                ])}
              />
            </>
          )}

          {d.bill_payment && (
            <Callout tone="acc" style={{ marginTop: 12 }}>
              Read as a <strong>credit card bill payment</strong>. That flips its sign
              depending on the account: money arriving on the card, money leaving the bank
              account that funds it.
            </Callout>
          )}

          {d.alert && (
            <>
              <Section title="Also reads as a bank alert"
                note={`Matched the "${d.alert.template}" template.`} />
              <div className="row tight" style={{ marginTop: 6 }}>
                <Chip tone={d.alert.direction === 'credit' ? 'pos' : 'neg'}>
                  {d.alert.direction === 'credit' ? 'money in' : 'money out'}
                </Chip>
                <Chip>₹{d.alert.amount}</Chip>
                {d.alert.counterparty && <Chip>{d.alert.counterparty}</Chip>}
                {d.alert.account_suffix && <Chip>account ⋯{d.alert.account_suffix}</Chip>}
                {d.alert.txn_date && <Chip>{d.alert.txn_date}</Chip>}
              </div>
            </>
          )}
        </Card>
      )}

      {m && (
        <Card title="The email" pad>
          <Section title="Who it is from" />
          <div className="row tight" style={{ margin: '6px 0 16px' }}>
            <Chip tone="acc">{m.institution || 'not recognised'}</Chip>
            {m.category && m.category !== 'unknown' && <Chip>{m.category}</Chip>}
            {m.matched_fragments?.length > 0 && (
              <span className="tiny dim">matched on {m.matched_fragments.join(', ')}</span>
            )}
          </div>

          <Section title="What each scan would do"
            note="A reason means the email is refused by that scan, and the reason is what you see beside it in the import list." />
          <Rows
            head={['Scan', 'Verdict']}
            rows={Object.entries(m.scans).map(([key, reason]) => [
              titleCase(key),
              reason ? <Chip tone="warn">{reason}</Chip> : <Chip tone="pos">kept</Chip>,
            ])}
          />

          {m.attachment_kept !== null && (
            <>
              <Section title="The attachment" />
              <div style={{ marginTop: 6 }}>
                {m.attachment_kept
                  ? <Chip tone="pos">downloaded</Chip>
                  : <Chip tone="warn">skipped as boilerplate</Chip>}
              </div>
            </>
          )}

          {m.password && (
            <>
              <Section title="If it is password-protected" />
              <div style={{ marginTop: 6 }}>
                <Chip tone="acc">{m.password.format}</Chip>
                <div className="tiny dim" style={{ marginTop: 6 }}>{m.password.explanation}</div>
              </div>
            </>
          )}

          {m.bureau && m.bureau !== 'unknown' && (
            <>
              <Section title="Recognised as a bureau report" />
              <div style={{ marginTop: 6 }}><Chip tone="acc">{m.bureau}</Chip></div>
            </>
          )}
          {m.portfolio_layout && m.portfolio_layout[0] !== 'unknown' && (
            <>
              <Section title="Recognised as a holdings statement" />
              <div style={{ marginTop: 6 }}><Chip tone="acc">{m.portfolio_layout[1]}</Chip></div>
            </>
          )}
        </Card>
      )}
    </>
  );
}

/* ────────────────────────────────────────────────────── institutions ───── */

function Institutions({ data, q }) {
  const rows = data.find.institutions.filter(
    (i) => !q || i.name.toLowerCase().includes(q) || i.match.some((f) => f.includes(q)));

  return (
    <Card
      title={`${rows.length} institutions`}
      sub="One record each. Adding a bank is one entry here and nothing else — these fields are what every list in the app is built from."
      pad={false}
    >
      <Rows
        head={['Name', 'Kind', 'Recognised by', 'Found by', 'PDF password']}
        rows={rows.map((i) => [
          <strong>{i.name}</strong>,
          <Chip tone={KIND_TONE[i.kind]}>{i.kind}</Chip>,
          <Chips list={i.match} />,
          i.sends.length ? <Chips list={i.sends} /> : <span className="tiny dim">no scan</span>,
          i.password ? (
            <>
              {i.password}
              {i.password_note && <div className="tiny dim">{i.password_note}</div>}
            </>
          ) : <span className="tiny dim">not documented</span>,
        ])}
      />
    </Card>
  );
}

/* ────────────────────────────────────────────────────── email filters ──── */

function EmailFilters({ data, q }) {
  const match = (s) => !q || String(s).toLowerCase().includes(q);
  return (
    <>
      <Card
        title="What each scan searches for"
        sub="Separate searches, because they want different documents over different windows."
        pad={false}
      >
        <Rows
          head={['Scan', 'Looks for', 'Attachment', 'Default window']}
          rows={data.find.scans.filter((s) => match(s.label) || match(s.key)).map((s) => [
            <>
              <strong>{s.label}</strong>
              <div className="tiny dim">{s.description}</div>
            </>,
            <Terms pattern={s.subjects.join('|')} max={8} />,
            s.needs_attachment ? 'required' : 'not needed',
            s.max_months ? `${s.max_months} months` : 'whole mailbox',
          ])}
        />
      </Card>

      <Card
        title="Why an email gets refused"
        sub="Checked in this order, and the reason is what you see beside the email in the import list."
        pad={false}
      >
        <Rows
          head={['Order', 'Reason shown', 'Triggered by']}
          rows={data.find.rejections.filter((r) => match(r.reason)).map((r, i) => [
            i + 1, <Chip tone="warn">{r.reason}</Chip>, <Terms pattern={r.pattern} max={10} />,
          ])}
        />
        <div className="small dim" style={{ padding: '10px 14px' }}>
          Then, in order: a subject that plainly says “statement” is kept; a known
          statement sender is kept; anything else is refused as “no statement signal”.
        </div>
      </Card>

      <Card
        title="Attachments skipped as boilerplate"
        sub="Card issuers attach terms and tariff sheets to the same email as the statement. Each one fails to parse and looks like an error."
      >
        <Terms pattern={data.find.skipped_filenames} max={30} />
      </Card>
    </>
  );
}

/* ──────────────────────────────────────────────────── reading documents ── */

function Reading({ data, q }) {
  const match = (s) => !q || String(s).toLowerCase().includes(q);
  const r = data.read;
  return (
    <>
      {data.open?.password_formats && (
        <Card
          title="Opening a locked PDF"
          sub="Indian banks lock an emailed statement with details you already know. Which issuer uses which is on its row under Institutions."
          pad={false}
        >
          <Rows
            head={['Format', 'What it needs from your profile', 'In words']}
            rows={data.open.password_formats
              .filter((f) => match(f.label) || match(f.explanation))
              .map((f) => [
                <strong>{f.label}</strong>,
                <Chips list={f.needs.map((n) => titleCase(n.replace(/_/g, ' ')))} />,
                f.explanation,
              ])}
          />
          <div className="small dim" style={{ padding: '10px 14px' }}>
            At most {data.open.max_candidates} candidates are tried per file. These are
            published formats built from your own details, not guesses — a brute-force
            space for an eight-character password is about 10<sup>14</sup> — and the cap
            also means a large profile can never turn this into something that hammers a
            file.
          </div>
        </Card>
      )}

      <Card title="Which account type a statement is"
        sub="Most specific first: “home loan” has to beat a bare “loan”." pad={false}>
        <Rows
          head={['Type', 'Recognised by']}
          rows={r.account_types.filter((a) => match(a.type) || match(a.pattern))
            .map((a) => [titleCase(a.type), <Terms pattern={a.pattern} />])}
        />
      </Card>

      <Card
        title="Column names the app understands"
        sub="Every bank names its columns differently, so headers are scored against these rather than kept as a per-bank template. Only the date is required — some card statements print a payment with no description at all."
        pad={false}
      >
        <Rows
          head={['Means', 'Header can say']}
          rows={Object.entries(r.columns)
            .filter(([role, aliases]) => match(role) || aliases.some(match))
            .map(([role, aliases]) => [
              <>
                <strong>{titleCase(role)}</strong>
                {r.required_columns.includes(role) && (
                  <Chip tone="acc" style={{ marginLeft: 6 }}>required</Chip>
                )}
              </>,
              <Chips list={aliases} />,
            ])}
        />
      </Card>

      <Card title="Card products"
        sub="A card's own name, so three cards from one bank can be told apart. Longest match wins.">
        <Chips
          list={Object.entries(r.card_variants).filter(([k, v]) => match(k) || match(v))
            .map(([, v]) => v)}
        />
      </Card>

      <Card
        title="Bank alert wordings"
        sub="An alert is a fixed sentence a bank's system generated, so each issuer's wording is read exactly or not at all. A guess at somebody's rent is worse than nothing."
        pad={false}
      >
        <Rows
          head={['Template', 'Direction', 'Kind']}
          rows={r.alert_templates.filter((t) => match(t.name) || match(t.pattern)).map((t) => [
            <span title={t.pattern}>{t.name}</span>,
            <Chip tone={t.direction === 'credit' ? 'pos' : 'neg'}>
              {t.direction === 'credit' ? 'money in' : 'money out'}
            </Chip>,
            t.kind,
          ])}
        />
        <div className="small dim" style={{ padding: '10px 14px' }}>
          Direction is fixed per template, never read from the verb: “Rs 500 debited…
          available balance credited” appears in one email often enough that reading
          whichever verb comes first gets the sign wrong.
        </div>
      </Card>

      <Card
        title="Credit report fields"
        sub="Found by label rather than by position, which survives a bureau changing its layout — and they do."
        pad={false}
      >
        <Rows
          head={['Field', 'Label can say']}
          rows={Object.entries(r.bureau_labels)
            .filter(([f, aliases]) => match(f) || aliases.some(match))
            .map(([field, aliases]) => [titleCase(field), <Chips list={aliases} />])}
        />
        <div className="small dim" style={{ padding: '10px 14px' }}>
          A score outside {r.bureau_score_range[0]}–{r.bureau_score_range[1]} is ignored —
          it is a page number or a postcode that happened to sit near the word “score”.
        </div>
      </Card>

      {r.account_number && (
        <Card title="Which number identifies the account" sub={r.account_number.note} pad={false}>
          <div className="small dim" style={{ padding: '10px 14px 0' }}>
            {r.account_number.shapes_first}
          </div>
          <Rows
            head={['Tried', 'The statement says', 'Which means']}
            rows={r.account_number.labels.filter((l) => match(l.label) || match(l.means))
              .map((l, i) => [i + 1, <strong>{l.label}</strong>, l.means])}
          />
          <div style={{ padding: '10px 14px' }}>
            <Section title="Never read as an account number"
              note="Each of these has cost a real ledger a split or a duplicated account." />
          </div>
          <Rows
            head={['Label', 'Why not']}
            rows={r.account_number.never.filter((n) => match(n.label) || match(n.why))
              .map((n) => [<strong>{n.label}</strong>, n.why])}
          />
          <div className="small dim" style={{ padding: '10px 14px' }}>
            {r.account_number.fallback} {r.account_number.stored_as}
          </div>
        </Card>
      )}

      {r.account_identity && (
        <Card title="When two statements are the same account" sub={r.account_identity.note} pad>
          <Callout tone="warn">{r.account_identity.why_it_matters}</Callout>
          <Rows head={['Tried in order', '']}
            rows={r.account_identity.fallbacks.map((f, i) => [i + 1, f])} />
        </Card>
      )}

      {r.person_vs_business && (
        <Card title="Telling a person from a business" sub={r.person_vs_business.note} pad={false}>
          <Rows head={['Signal', '']}
            rows={r.person_vs_business.signals.map((s, i) => [i + 1, s])} />
        </Card>
      )}

      <Card
        title="Holdings statements"
        sub="Tried in this order. The depository layout goes first because a consolidated statement names the brokers whose holdings it consolidates."
        pad={false}
      >
        <Rows
          head={['Layout', 'Recognised by']}
          rows={r.portfolio_layouts.filter((l) => match(l.provider) || l.match.some(match))
            .map((l) => [<strong>{l.provider}</strong>, <Chips list={l.match} />])}
        />
        <div className="small dim" style={{ padding: '10px 14px' }}>
          A document mentioning {r.trade_markers.slice(0, 3).join(', ')} or similar is read
          as a record of <strong>trades</strong>, not holdings — its quantities are what
          changed hands, not what you own.
        </div>
      </Card>
    </>
  );
}

/* ──────────────────────────────────────────────────────── categories ───── */

function Categories({ data, q }) {
  const rows = data.check.categories.filter((r) => !q || r.category.includes(q)
    || r.pattern.toLowerCase().includes(q) || r.group.toLowerCase().includes(q));

  return (
    <Card
      title={`${rows.length} rules, in order`}
      sub="The first match wins, so a specific rule must come before a general one: “HDFC HOME LOAN EMI” has to be seen as an EMI before the bare “HDFC” is seen as a bank transfer."
      pad={false}
    >
      <Rows
        head={['#', 'Category', 'Applies to', 'Looks for', 'Unless']}
        rows={rows.map((r) => [
          r.order,
          <>
            <Chip tone="acc">{titleCase(r.category)}</Chip>
            <div className="tiny dim">{r.group} · {Math.round(r.confidence * 100)}%</div>
          </>,
          r.direction ? (r.direction === 'credit' ? 'money in' : 'money out') : 'either',
          <Terms pattern={r.pattern} max={10} />,
          r.excludes ? <Terms pattern={r.excludes} max={6} /> : <span className="dim">—</span>,
        ])}
      />
    </Card>
  );
}

/* ───────────────────────────────────────────────────────────── ledger ──── */

function LedgerRules({ data, q }) {
  const L = data.ledger;
  if (!L) {
    return <Callout tone="warn">This build of the server does not publish the ledger rules yet.</Callout>;
  }
  const match = (s) => !q || String(s).toLowerCase().includes(q);
  const a = L.attribution;

  return (
    <>
      <Card title="Which way the money went"
        sub="Signals, strongest first. Every row records which one decided it." pad={false}>
        <Rows
          head={['Signal', 'Why it ranks there']}
          rows={L.directions.filter((d) => match(d.label) || match(d.detail)).map((d) => [
            <Chip tone={d.strength <= 2 ? 'pos' : d.strength >= 5 ? 'neg' : ''}>{d.label}</Chip>,
            d.detail,
          ])}
        />
      </Card>

      <Card
        title="Which month a transaction counts in"
        sub="A salary paid on the 31st and again on the 1st is one month's pay, not two — and the guard against that can itself create the problem it prevents."
        pad
      >
        <div className="small dim" style={{ marginBottom: 10 }}>{a.default}</div>
        <div className="rule-list">
          {a.steps.filter((s) => match(s.name) || match(s.detail)).map((s, i) => (
            <div className="list-row" key={s.name}>
              <span className="tiny dim num" style={{ width: 18 }}>{i + 1}</span>
              <div className="grow">
                <div className="list-title">{s.name}</div>
                <div className="list-sub">{s.detail}</div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card
        title="What counts as spending"
        sub="Category says what money was spent on. This says whether it was spent at all — and exactly one applies to each row, so no total double-counts."
        pad={false}
      >
        <Rows
          head={['Role', 'Counts as', 'Meaning']}
          rows={L.flow_roles.filter((r) => match(r.role) || match(r.note)).map((r) => [
            <Chip tone={r.counts_as === 'income' ? 'pos' : r.counts_as === 'spending' ? 'neg' : ''}>
              {titleCase(r.role)}
            </Chip>,
            r.counts_as,
            r.note,
          ])}
        />
      </Card>

      <Card
        title="Why two rows were matched"
        sub="Pairing keeps one movement from being counted twice. Every kind is bounded, because arithmetic alone will always find a coincidence in a large ledger."
        pad={false}
      >
        <Rows
          head={['Kind', 'What has to be true']}
          rows={L.pairing.filter((p) => match(p.name) || match(p.note))
            .map((p) => [<strong>{p.name}</strong>, p.note])}
        />
      </Card>

      <Card title="Recurring charges" sub={L.recurring.note} pad={false}>
        <Rows
          head={['Cadence', 'Every', 'Give or take']}
          rows={L.cadences.filter((c) => match(c.name))
            .map((c) => [titleCase(c.name), `${c.days} days`, `${c.tolerance_days} days`])}
        />
        <div className="small dim" style={{ padding: '10px 14px' }}>
          Seen at least {L.recurring.min_occurrences} times, with the amount varying by no
          more than {Math.round(L.recurring.amount_variance * 100)}%.
        </div>
      </Card>

      <Card title="Alerts and the statements that replace them" sub={L.alerts.note}>
        <div className="small dim">
          A statement row within {L.alerts.supersede_days} days of an alert, for the same
          account and amount, is the same payment.
        </div>
      </Card>

      <Card title="Matching a credit report to your accounts" sub={L.bureau_matching.note}>
        <div className="small dim">
          Linked automatically at {Math.round(L.bureau_matching.auto_link * 100)}%
          confidence, suggested at {Math.round(L.bureau_matching.suggest * 100)}%, and never
          on a lender&apos;s name alone.
        </div>
        <div className="row tight" style={{ marginTop: 8 }}>
          {L.bureau_matching.never_expected.map((t) => <Chip key={t}>{titleCase(t)}</Chip>)}
        </div>
        <div className="small dim" style={{ marginTop: 6 }}>
          Never reported on, so their absence is never flagged as a gap.
        </div>
      </Card>
    </>
  );
}

/* ──────────────────────────────────────────────────────────── numbers ──── */

const THRESHOLD_COLS = ['28%', '14%', '58%'];

function Numbers({ data, q }) {
  const rows = data.thresholds.filter((t) => !q || t.name.toLowerCase().includes(q)
    || t.group.toLowerCase().includes(q) || t.why.toLowerCase().includes(q));
  const groups = [...new Set(rows.map((t) => t.group))];

  const show = (t) => {
    if (t.unit === 'money') return `₹${t.value}`;
    if (t.unit === 'ratio') {
      const n = Number(t.value);
      return n <= 1 ? `${Math.round(n * 100)}%` : t.value;
    }
    const unit = Number(t.value) === 1 ? t.unit.replace(/s$/, '') : t.unit;
    return `${t.value} ${unit}`;
  };

  return (
    <>
      <Callout>
        These are code, not settings — they are the same for every import and cannot be
        changed from here. The reason each one is what it is matters more than the number.
      </Callout>
      {/* One card per group, all the same three columns. Left to size
          themselves, "Value" landed in a different place in each card. */}
      {groups.map((group) => (
        <Card key={group} title={group} pad={false}>
          <Rows
            cols={THRESHOLD_COLS}
            head={['', 'Value', 'Why']}
            rows={rows.filter((t) => t.group === group).map((t) => [
              <strong>{t.name}</strong>,
              <span className="num nowrap">{show(t)}</span>,
              <>
                {t.why || <span className="dim">—</span>}
                <div className="tiny dim" style={{ marginTop: 2 }}>{t.source}</div>
              </>,
            ])}
          />
        </Card>
      ))}
    </>
  );
}

/* ───────────────────────────────────────────────────────── vocabulary ──── */

function Vocabulary({ data, q }) {
  const v = data.vocabulary;
  const match = (s) => !q || String(s).toLowerCase().includes(q);

  return (
    <>
      <Card
        title="Payment rails"
        sub="Network and instrument codes. Which ones apply depends on the job, and the two lists differ on purpose."
        pad
      >
        <Section title="Stripped from the front of a narration"
          note="Anything leading the description is noise that hides the merchant." />
        <div style={{ margin: '6px 0 14px' }}><Chips list={v.prefix_rails} match={match} /></div>
        <Section title="Removed when matching a recurring charge"
          note="Narrower: stripping “CASH” or “POS” would merge unrelated withdrawals into one series." />
        <div style={{ marginTop: 6 }}><Chips list={v.signature_rails} match={match} /></div>
      </Card>

      <Card
        title="Credit card bill payments"
        sub="Three parts of the app ask whether a row is a card bill, for three different reasons. The wordings they all share are here; the rest belong to whoever needs them."
        pad
      >
        <Section title="All three agree" />
        <div style={{ margin: '6px 0 14px' }}>
          <Chips list={v.bill_payment.shared.flatMap(terms)} match={match} />
        </div>
        <Section title="Only when deciding direction" />
        <div style={{ margin: '6px 0 14px' }}>
          <Chips list={v.bill_payment.direction_only.flatMap(terms)} match={match} />
        </div>
        <Section title="Only when deciding category"
          note="“BBPS payment received” is a card bill on a card, but money arriving on a bank account — so the direction reader must not see it." />
        <div style={{ margin: '6px 0 14px' }}>
          <Chips list={v.bill_payment.category_only.flatMap(terms)} match={match} />
        </div>
        <Section title="Only when matching a settlement" />
        <div style={{ marginTop: 6 }}>
          <Chips list={v.bill_payment.settlement_only.flatMap(terms)} match={match} />
        </div>
      </Card>

      <Card
        title="Blank figures"
        sub="Read as “nothing reported”, never as zero. A bureau printing “-” for a closed account means it has no figure; recording ₹0 would put a confident number where there is none."
      >
        <Chips list={v.no_figure} match={match} />
      </Card>

      <Card title="Months" sub="Every spelling that appears on these documents.">
        <Chips list={v.months} match={match} />
      </Card>
    </>
  );
}

/* ─────────────────────────────────────────────────────────── pipeline ──── */

function Pipeline({ data, q }) {
  const p = data.pipeline;
  if (!p) return <Callout tone="warn">This server does not publish the pipeline yet.</Callout>;
  const match = (s) => !q || String(s).toLowerCase().includes(q);

  return (
    <>
      <Card
        title="The order things happen in"
        sub="Order is a rule here, not an implementation detail — each step is placed where it is because of what would break elsewhere."
        pad
      >
        <div className="rule-list">
          {p.stages.filter((s) => match(s.name) || match(s.why)).map((s, i) => (
            <div className="list-row" key={s.name}>
              <span className="tiny dim num" style={{ width: 18 }}>{i + 1}</span>
              <div className="grow">
                <div className="list-title">{s.name}</div>
                <div className="list-sub">{s.why}</div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Which reader a document goes to"
        sub="Tried in this order. The statement reader is last on purpose." pad={false}>
        <Rows
          head={['Reader', 'What has to be true']}
          rows={p.classification_order.filter((c) => match(c.reader) || match(c.test))
            .map((c) => [<strong>{c.reader}</strong>, c.test])}
        />
      </Card>

      <Card title="How a file's format is decided" sub={p.formats.note} pad={false}>
        <Rows
          head={['First bytes', 'Read as']}
          rows={p.formats.magic_bytes.filter((m) => match(m.kind))
            .map((m) => [<code>{m.bytes}</code>, m.kind.toUpperCase()])}
        />
        {/* This row of chips sat here unlabelled, under a card whose subtitle
            is about magic bytes - so it read as a list of something, of
            nothing in particular. The card below it labels its own chips; so
            does this one now. */}
        <div style={{ padding: '10px 14px' }}>
          <Chips list={p.formats.extensions} match={match} />
          <div className="small dim" style={{ marginTop: 6 }}>
            The extensions an upload will accept. Which reader runs is still decided by
            what is inside the file, so a .csv holding a PDF is read as a PDF.
          </div>
        </div>
      </Card>

      <Card title="Long-running work" sub={p.jobs.note}>
        <Chips list={p.jobs.terminal_states} />
        <div className="small dim" style={{ marginTop: 6 }}>
          A job in one of these states is finished for good.
        </div>
      </Card>
    </>
  );
}

/* ────────────────────────────────────────────────────────────── money ──── */

function Money({ data, q }) {
  const m = data.money;
  if (!m) return <Callout tone="warn">This server does not publish the forecast rules yet.</Callout>;
  const match = (s) => !q || String(s).toLowerCase().includes(q);

  return (
    <>
      <Card title="How the forecast is built" sub={m.forecast.note} pad>
        <Section title="Why every month is a range, never a number" />
        <p className="lead" style={{ margin: '6px 0 14px' }}>{m.forecast.band_note}</p>
        <div className="small dim" style={{ marginBottom: 14 }}>
          The band is never narrower than {Math.round(m.forecast.min_band_share * 100)}% of
          the median month.
        </div>

        <Section title="How much to trust it" />
        <Rows
          head={['Says', 'When']}
          rows={m.forecast.confidence.filter((c) => match(c.level) || match(c.needs)).map((c) => [
            <Chip tone={c.level === 'high' ? 'pos' : c.level === 'low' ? 'warn' : ''}>
              {c.level}
            </Chip>,
            c.needs,
          ])}
        />
        <div className="small dim" style={{ margin: '8px 0 14px' }}>
          A recurring series counts as committed money at{' '}
          {Math.round(m.forecast.committed_confidence * 100)}% confidence or better.
        </div>

        <Callout tone="warn">{m.forecast.limits}</Callout>
      </Card>

      <Card title="Loan maths" sub={m.loans.note}>
        <div className="small dim">{m.loans.rate_recovery}</div>
      </Card>
    </>
  );
}

/* ────────────────────────────────────────────────────────────── model ──── */

function Model({ data, q }) {
  const m = data.model;
  const p = data.privacy;
  const match = (s) => !q || String(s).toLowerCase().includes(q);

  return (
    <>
      {m && (
        <Callout tone={m.enabled ? 'warn' : 'pos'}>
          The model is currently <strong>{m.enabled ? 'on' : 'off'}</strong>.
          {m.enabled
            ? ' Narrations that no rule recognises are sent for naming.'
            : ' Nothing is sent anywhere. You can turn it on in Settings.'}
        </Callout>
      )}

      {m && (
        <Card title="What it is used for" sub="And, more importantly, what it is never used for." pad>
          <Section title="Used for" />
          <div className="col" style={{ gap: 3, margin: '6px 0 14px' }}>
            {m.used_for.filter(match).map((u) => (
              <div className="small" key={u}>· {u}</div>
            ))}
          </div>
          <Section title="Never used for" />
          <div className="col" style={{ gap: 3, marginTop: 6 }}>
            {m.never_used_for.filter(match).map((u) => (
              <div className="small muted" key={u}>· {u}</div>
            ))}
          </div>
        </Card>
      )}

      {p && (
        <Card title="Removed from every narration first" sub={p.note} pad={false}>
          <Rows
            head={['What', 'Why']}
            rows={p.removed.filter((r) => match(r.what) || match(r.why))
              .map((r) => [<span title={r.pattern}><strong>{r.what}</strong></span>, r.why])}
          />
        </Card>
      )}

      {m && (
        <Card
          title="Exactly what it is told"
          sub={`The instructions sent with every batch of ${m.batch_size} descriptions.`}
        >
          <pre className="code-block" style={{ maxHeight: 340, overflowY: 'auto' }}>
            {m.instructions}
          </pre>
        </Card>
      )}
    </>
  );
}

/* ──────────────────────────────────────────────────────────── storage ──── */

function Storage({ data, q }) {
  const s = data.storage;
  if (!s) return <Callout tone="warn">This server does not publish the storage rules yet.</Callout>;
  const match = (t) => !q || String(t).toLowerCase().includes(q);

  return (
    <Card title="What each clearing action keeps" sub={s.snapshot_note} pad={false}>
      <Rows
        head={['Clearing', 'Tables', 'What it is']}
        rows={s.scopes.filter((sc) => match(sc.scope) || match(sc.note)).map((sc) => [
          <strong>{titleCase(sc.scope.replace(/_/g, ' '))}</strong>, sc.tables, sc.note,
        ])}
      />
      <div className="small dim" style={{ padding: '10px 14px' }}>
        The last {s.max_snapshots} snapshots are kept; the oldest is pruned. These actions
        live on the Data screen.
      </div>
    </Card>
  );
}

