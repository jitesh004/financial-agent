/* Everything the pipeline could not settle on its own.
 *
 * The queue exists because the alternatives are both worse: guessing silently
 * moves money between income and spending with nothing to show for it, and
 * refusing to guess leaves the dashboard incomplete until every item is
 * cleared. So a safe default is applied, the figure is always complete, and
 * the items that carried a judgement call are listed here.
 *
 * Two modes, because they are two different jobs rather than two screens over
 * one queue: one row at a time with its full context, or many at once grouped
 * by merchant. Finishing one used to leave the other still showing a backlog,
 * which is why they are one destination now.
 *
 * Worth knowing while working through it: for an ambiguous inbound amount, net
 * savings is the same either way. Only the split between income and spending
 * moves.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { usePeriod } from '../core/period';
import { useToast } from '../core/toast';
import { useCategories } from '../core/ledger';
import { count, dateLabel, money, titleCase } from '../core/format';
import {
  Button, Callout, Card, Chip, Empty, Loading, Search, Segmented, Select, Stat, Table,
} from '../ui';

const MODES = [
  ['queue', 'One at a time', 'Each row with its context, for the ones that need a judgement call.'],
  ['bulk', 'By merchant', 'Group identical merchants and categorise them in one go.'],
];

export default function Review() {
  const [mode, setMode] = useRouteParam('mode', 'queue');
  const active = MODES.find((m) => m[0] === mode) || MODES[0];

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h2 className="h2">Review</h2>
          <p className="lead">{active[2]}</p>
        </div>
        <Segmented value={mode} onChange={setMode} ariaLabel="Review mode"
          options={MODES.map(([v, l, hint]) => [v, l, hint])} />
      </div>
      {mode === 'bulk' ? <ByMerchant /> : <Queue />}
    </>
  );
}

/* ── one at a time ───────────────────────────────────────────────────────── */

const ROLES = [
  ['income', 'Income — money that genuinely came in'],
  ['claim_settlement', 'Money back — repays something already counted as spending'],
  ['refund', 'Refund — a merchant returning money'],
  ['card_settlement', 'Card payment — settles a card bill, never income'],
  ['transfer_in', 'Transfer — between your own accounts'],
  ['excluded', 'Ignore — leave out of every total'],
];

/* How many rows of one group to put in the DOM at once.
 *
 * Review is the screen most likely to be holding a backlog - it is where
 * everything the pipeline could not place ends up - and it rendered every one
 * of them. Two hundred rows is sixteen screenfuls of scrolling with a <select>
 * in each; two thousand, which is what the bulk query asks for, is not a page
 * at all. A queue is worked top-down, so a page of it plus a way to ask for
 * more is both faster and a better shape than an endless list. */
const PAGE = 40;

function usePaged(rows, step = PAGE) {
  const [shown, setShown] = useState(step);
  // A new group, or a row resolved out of this one, must not leave the page
  // size stranded where it was.
  useEffect(() => { setShown(step); }, [rows.length === 0, step]);
  return {
    slice: rows.length > shown ? rows.slice(0, shown) : rows,
    hidden: Math.max(0, rows.length - shown),
    more: () => setShown((n) => n + step),
    all: () => setShown(rows.length),
  };
}

function MoreRows({ hidden, total, shown, onMore, onAll }) {
  if (!hidden) return null;
  return (
    <div className="row" style={{ padding: '10px 16px', borderTop: '1px solid var(--line)' }}>
      <span className="small dim">Showing {count(shown)} of {count(total)}</span>
      <div className="spacer" />
      <Button size="sm" onClick={onMore}>Show {Math.min(hidden, PAGE)} more</Button>
      {hidden > PAGE && <Button size="sm" onClick={onAll}>Show all {count(total)}</Button>}
    </div>
  );
}

const REASON_HINT = {
  unknown_funding:
    'No bank statement covering this date is loaded, so there is no way to tell whether '
    + 'you funded this or somebody else did. Loading that month resolves it without a '
    + 'decision.',
};

function Queue() {
  const toast = useToast();
  const { params, paramsKey, label: periodLabel, scoped } = usePeriod();
  const { data, loading, error, refetch } = useQuery(
    `review:${paramsKey}`, () => api.reviewQueue(params));
  const [busy, setBusy] = useState(null);

  const items = data?.transactions || [];

  // Grouped by the question being asked, so a run of identical decisions can be
  // worked through as one thought rather than N.
  const groups = useMemo(() => {
    const out = new Map();
    for (const t of items) {
      const k = t.review_reason || 'Needs a look';
      if (!out.has(k)) out.set(k, []);
      out.get(k).push(t);
    }
    return [...out.entries()];
  }, [items]);

  async function resolve(txn, fields) {
    setBusy(txn.id);
    try {
      /* TransactionUpdateReq has no `needs_review` field - it is not something
         a user sets directly, it is cleared as a side effect of recording an
         actual decision. Sending only `{needs_review: false}`, as "looks right"
         used to, matched no field on the model, so the whole payload
         round-tripped to an empty dict and the row silently never left the
         queue. Confirming the flow_role the pipeline already chose IS the
         decision "looks right" means to record. */
      await api.updateTransaction(txn.id, { flow_role: txn.flow_role, ...fields });
      invalidate('review', 'workflow', 'analysis', 'dashboard', 'txns');
      await refetch();
    } catch (e) {
      toast.fail('That decision was not recorded', e.message);
    } finally { setBusy(null); }
  }

  if (loading) return <Loading label="Reading the queue…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  return (
    <>
      <Callout tone="acc">
        Each of these already has a sensible answer applied, so your totals are complete.
        Confirming just makes them right rather than likely — and your choice is
        remembered even if the statement is re-parsed.
      </Callout>

      {!items.length && (
        <Empty icon="check-circle"
          title={scoped ? `Nothing to review in ${periodLabel}` : 'Nothing to review'}>
          {scoped
            ? 'Every transaction counted in this period was classified confidently. '
              + 'Widen the period to check the rest of the ledger.'
            : 'Every transaction was classified confidently. New items appear here when a '
              + 'card payment has no matching bank debit, when several payments look like '
              + 'one settlement, or when money arrives that could be either income or a '
              + 'repayment.'}
        </Empty>
      )}

      {groups.map(([reason, rows]) => (
        <QueueGroup key={reason} reason={reason} rows={rows} busy={busy} resolve={resolve} />
      ))}
    </>
  );
}

function QueueGroup({ reason, rows, busy, resolve }) {
  const paged = usePaged(rows);
  return (
        <Card title={reason}
          sub={`${rows.length} transaction${rows.length === 1 ? '' : 's'}`} pad={false}>
          {REASON_HINT[reason] && (
            <div style={{ padding: '12px 16px 0' }}>
              <Callout tone="warn">{REASON_HINT[reason]}</Callout>
            </div>
          )}
          <Table>
            <thead>
              <tr>
                <th>Date</th><th>Description</th>
                <th className="right">Amount</th><th>Counts as</th>
              </tr>
            </thead>
            <tbody>
              {paged.slice.map((t) => (
                <tr key={t.id} style={{ opacity: busy === t.id ? 0.45 : 1 }}>
                  <td className="nowrap">{dateLabel(t.date)}</td>
                  <td>
                    <div style={{ overflowWrap: 'anywhere', lineHeight: 1.4 }}>
                      {t.description}
                    </div>
                    <div className="row tight" style={{ marginTop: 4 }}>
                      <Chip>{titleCase(t.category)}</Chip>
                      {t.flow_role && (
                        <Chip tone="acc">now: {titleCase(t.flow_role.replace(/_/g, ' '))}</Chip>
                      )}
                    </div>
                  </td>
                  <td className="right num nowrap"
                    style={{ color: t.direction === 'credit' ? 'var(--pos)' : 'var(--text)' }}>
                    {t.direction === 'credit' ? '+' : '−'}{money(Math.abs(t.amount))}
                  </td>
                  <td>
                    <div className="row tight">
                      <Select
                        value={t.flow_role || ''}
                        placeholder="Choose…"
                        disabled={busy === t.id}
                        onChange={(v) => resolve(t, { flow_role: v })}
                        options={ROLES}
                        /* Was maxWidth 220, which cut "Income - money that
                           genuinely came in" off mid-word in the closed
                           control. The column has the room; let it use it. */
                        style={{ flex: 1, minWidth: 200, maxWidth: 360 }}
                      />
                      <Button size="sm" disabled={busy === t.id}
                        onClick={() => resolve(t, {})}
                        title="Accept what the app already decided">
                        Looks right
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
          <MoreRows hidden={paged.hidden} total={rows.length} shown={paged.slice.length}
            onMore={paged.more} onAll={paged.all} />
        </Card>
  );
}

/* ── by merchant ─────────────────────────────────────────────────────────── */

/* Categorising one transaction at a time is the wrong unit of work: 476
   unlabelled rows on one real ledger were 285 distinct merchants, and most of
   those repeat. Deciding once per merchant and applying it to every matching
   row is roughly twenty times less clicking, and it is also what the learned
   cache stores - so the same decision holds for next month's statement. */
const SORTS = [['value', 'Largest first'], ['count', 'Most frequent'], ['name', 'Name']];

function ByMerchant() {
  const toast = useToast();
  const { params, paramsKey, label: periodLabel, scoped } = usePeriod();
  const { data: categories = [] } = useCategories();
  const [scope, setScope] = useState('uncategorized');
  const [sort, setSort] = useState('value');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [busy, setBusy] = useState(null);
  const [done, setDone] = useState(0);

  const query = useMemo(() => {
    const q = { limit: 2000, ...params };
    if (scope === 'uncategorized') q.category = 'uncategorized';
    if (scope === 'review') q.needs_review = true;
    return q;
  }, [scope, params]);

  const { data, loading, error, refetch } = useQuery(
    `review-bulk:${scope}:${paramsKey}`, () => api.transactions(query));

  const rows = data?.transactions || [];

  // One entry per merchant, because that is the unit a decision applies to.
  const groups = useMemo(() => {
    const byKey = new Map();
    for (const t of rows) {
      const key = (t.merchant || t.description || '').trim().toUpperCase().slice(0, 48)
        || '(no description)';
      if (!byKey.has(key)) byKey.set(key, { key, items: [], total: 0 });
      const g = byKey.get(key);
      g.items.push(t);
      g.total += Math.abs(Number(t.amount) || 0);
    }
    let out = [...byKey.values()];
    if (search.trim()) {
      const q = search.trim().toUpperCase();
      out = out.filter((g) => g.key.includes(q));
    }
    const cmp = {
      value: (a, b) => b.total - a.total,
      count: (a, b) => b.items.length - a.items.length,
      name: (a, b) => a.key.localeCompare(b.key),
    }[sort];
    return out.sort(cmp);
  }, [rows, sort, search]);

  async function assign(group, category) {
    if (!category) return;
    setBusy(group.key);
    try {
      // One request for the whole merchant. The backend also writes it to the
      // learned merchant cache, so next month's statement arrives already
      // categorised rather than back in this queue.
      await api.bulkUpdate(group.items.map((t) => t.id), { category });
      invalidate('review', 'workflow', 'analysis', 'dashboard', 'txns');
      await refetch();
      setDone((n) => n + group.items.length);
    } catch (e) {
      toast.fail('That did not apply', e.message);
    } finally { setBusy(null); }
  }

  const remaining = groups.reduce((n, g) => n + g.items.length, 0);
  const value = groups.reduce((n, g) => n + g.total, 0);

  const paged = usePaged(groups, 25);

  return (
    <>
      {error && <Callout tone="neg">{error.message}</Callout>}
      {done > 0 && (
        <Callout tone="pos">
          {done} transaction{done === 1 ? '' : 's'} categorised this session. Each merchant
          is remembered, so it will not come back.
        </Callout>
      )}

      <div className="grid cols-3">
        <Stat label="Merchants to decide" value={String(groups.length)} />
        <Stat label="Transactions" value={String(remaining)} />
        <Stat label="Value" value={value} tone="warn" />
      </div>

      <div className="row">
        <Segmented
          value={scope} onChange={setScope} ariaLabel="Which rows"
          options={[['uncategorized', 'Uncategorised'], ['review', 'Needs review'],
            ['all', 'Everything']]}
        />
        <Select value={sort} onChange={setSort} options={SORTS} aria-label="Sort" />
        <Search value={search} onChange={setSearch} placeholder="Filter merchants…"
          style={{ flex: 1, minWidth: 180 }} />
      </div>

      {loading && <Loading label="Grouping by merchant…" />}

      {!loading && !groups.length && (
        <Empty icon="check-circle"
          title={scoped ? `Nothing left to categorise in ${periodLabel}`
            : 'Nothing left to categorise'}>
          Every transaction in this view has a category. Switch the filter above
          {scoped && ', or widen the period,'} to review other groups.
        </Empty>
      )}

      {/* One card per merchant, and a real ledger has hundreds of them. The
          expanded row list inside each is already bounded; this bounds the
          list of cards. */}
      {paged.slice.map((g) => (
        <Card key={g.key} pad>
          <div className="row" style={{ opacity: busy === g.key ? 0.5 : 1 }}>
            <div className="grow">
              <div style={{ fontWeight: 620 }}>{g.key}</div>
              <div className="small muted">
                {g.items.length} transaction{g.items.length === 1 ? '' : 's'} · {money(g.total)} ·{' '}
                <button className="btn link"
                  onClick={() => setExpanded(expanded === g.key ? null : g.key)}>
                  {expanded === g.key ? 'hide' : 'show'} transactions
                </button>
              </div>
            </div>
            <Select
              value=""
              placeholder={`Categorise all ${g.items.length}…`}
              disabled={busy === g.key}
              onChange={(v) => assign(g, v)}
              options={categories.map((c) => [c, titleCase(c)])}
              style={{ minWidth: 200 }}
            />
          </div>

          {expanded === g.key && (
            <Table scrollY className="on-2" maxHeight={280}>
              <thead>
                <tr><th>Date</th><th>Description</th><th className="right">Amount</th></tr>
              </thead>
              <tbody>
                {g.items.map((t) => (
                  <tr key={t.id}>
                    <td className="nowrap">{dateLabel(t.date)}</td>
                    <td><div style={{ overflowWrap: 'anywhere' }}>{t.description}</div></td>
                    <td className="right num nowrap">{money(Math.abs(t.amount))}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      ))}

      {paged.hidden > 0 && (
        <div className="row">
          <span className="small dim">
            Showing {count(paged.slice.length)} of {count(groups.length)} merchants
          </span>
          <div className="spacer" />
          <Button size="sm" onClick={paged.more}>Show {Math.min(paged.hidden, 25)} more</Button>
          {paged.hidden > 25 && (
            <Button size="sm" onClick={paged.all}>Show all {count(groups.length)}</Button>
          )}
        </div>
      )}

      {rows.length >= 2000 && (
        <Callout tone="warn">
          Showing the first {count(2000)} rows in this filter. Narrow the period to work
          through the rest.
        </Callout>
      )}
    </>
  );
}
