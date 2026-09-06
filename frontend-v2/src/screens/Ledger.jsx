/* Every transaction, with every filter the app can apply.
 *
 * The version this replaces had already folded Savings, Cards, UPI and EMI
 * into one table with presets, and that decision is kept. What is not kept is
 * how it moved between them: each preset replaced the table wholesale, because
 * React otherwise carried the account selection out of one preset and into the
 * next, and a view with 28 matching rows rendered "no transactions match". A
 * remount and a refetch, to reset one piece of state. Here the preset resets
 * that state itself, on one table that never unmounts - and each preset is a
 * URL, so "my card spending in August" is a link you can send yourself.
 *
 * The table is virtualised (see ui/virtual.jsx). A page of a thousand rows
 * mounts about forty of them, which is what keeps typing in the filter box
 * responsive on a ledger that size.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { usePeriod } from '../core/period';
import { usePrefs } from '../core/prefs';
import { useToast } from '../core/toast';
import { useAccounts, useCategories } from '../core/ledger';
import {
  count, dateLabel, downloadCsv, money, monthLabel, titleCase, toCsv, today,
} from '../core/format';
import {
  Button, Callout, Card, Chip, Empty, Icon, IconButton, Loading, PromptButton, Search,
  Segmented, Select, Toggle,
} from '../ui';
import { VirtualBody } from '../ui/virtual';

const SAVINGS = new Set(['savings', 'current', 'wallet']);

const VIEWS = [
  {
    key: 'all',
    label: 'All',
    hint: 'Every account, every filter available.',
    accounts: (all) => all,
  },
  {
    key: 'bank',
    label: 'Bank',
    hint: 'Money actually in the bank. Cards and loans have their own presets.',
    accounts: (all) => all.filter((a) => SAVINGS.has(a.account_type)),
    empty: 'No savings or current accounts found.',
  },
  {
    key: 'cards',
    label: 'Cards',
    hint: 'Credit cards only, with a UPI/other split — a lot of Indian card spend is '
      + 'routed through UPI rather than a swipe.',
    accounts: (all) => all.filter((a) => a.account_type === 'credit_card'),
    rails: true,
    empty: 'No credit cards found.',
  },
  {
    key: 'upi',
    label: 'UPI',
    hint: 'UPI across every account. It is a payment rail, not an account type, and most '
      + 'of it happens straight off a bank account.',
    accounts: (all) => all,
    fixedRail: 'upi',
  },
  {
    key: 'emi',
    label: 'EMI',
    hint: 'What was actually paid, month by month — which can differ from the nominal EMI '
      + 'when there is a part-payment or a missed month.',
    accounts: (all) => all,
    fixedCategory: 'emi',
  },
];

const SORTS = [
  ['date', 'Date'], ['amount', 'Amount'], ['description', 'Description'],
  ['category', 'Category'],
];

/* Where a category came from. Shown so somebody can tell a hard rule from a
   model guess, and knows which ones are worth checking. */
const SOURCE_TONE = {
  rule: '', merchant_cache: '', transfer_match: '', llm: 'warn', user: 'pos', default: 'warn',
};
const SOURCE_LABEL = {
  rule: 'rule', merchant_cache: 'learned', transfer_match: 'matched',
  llm: 'model', user: 'you', default: 'guess',
};

export default function Ledger() {
  const toast = useToast();
  const { params: periodParams, paramsKey, label: periodLabel, scoped } = usePeriod();
  const [prefs] = usePrefs();
  const { data: accounts = [] } = useAccounts();
  const { data: categories = [] } = useCategories();

  const [view, setView] = useRouteParam('view', 'all');
  const [category, setCategory] = useRouteParam('cat', '');
  const [rail, setRail] = useRouteParam('rail', '');
  const [search, setSearch] = useRouteParam('q', '');
  const [sortBy, setSortBy] = useState('date');
  const [sortDir, setSortDir] = useState('desc');
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState(() => new Set());
  const [saving, setSaving] = useState(null);
  const [explaining, setExplaining] = useState(null);
  const [dropped, setDropped] = useState(() => new Set());

  const scrollRef = useRef(null);
  const active = VIEWS.find((v) => v.key === view) || VIEWS[0];
  const inScope = useMemo(() => active.accounts(accounts), [active, accounts]);
  const scopeIds = useMemo(() => inScope.map((a) => a.id), [inScope]);

  /* Accounts the user has explicitly unticked. Held as an exclusion set rather
     than a selection so a newly-arrived account (after a retry, say) defaults
     to included instead of silently missing. */
  useEffect(() => { setDropped(new Set()); setPage(0); }, [view]);
  useEffect(() => { setPage(0); }, [category, rail, sortBy, sortDir, paramsKey, search]);

  const selectedIds = scopeIds.filter((id) => !dropped.has(id));
  /* Always an explicit list - never omit the filter just because every account
     IN THIS VIEW is selected. `inScope` is often a pre-filtered subset, and
     "all of my subset" is not "no filter at all": omitting it let a scoped
     preset silently show every OTHER account's rows too. Zero selected must
     return zero rows for the same reason "clear all" implies emptiness. */
  const accountParam = selectedIds.length ? selectedIds.join(',') : '__none__';

  const pageSize = Number(prefs.pageSize) || 250;
  const query = useMemo(() => ({
    account_id: accountParam,
    category: active.fixedCategory || category || undefined,
    rail: active.fixedRail || rail || undefined,
    search: search || undefined,
    sort_by: sortBy,
    sort_dir: sortDir,
    offset: page * pageSize,
    limit: pageSize,
    ...periodParams,
  }), [accountParam, active, category, rail, search, sortBy, sortDir, page, pageSize,
       periodParams]);

  const key = `txns:${JSON.stringify(query)}`;
  const { data, loading, fetching, error, refetch } =
    useQuery(key, () => api.transactions(query));

  const rows = data?.transactions || [];
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / pageSize) || 1;
  const accountName = useCallback(
    (id) => accounts.find((a) => a.id === id)?.display_name || '—', [accounts]);

  const visible = useMemo(
    () => (prefs.hideExcluded ? rows.filter((r) => !r.excluded) : rows),
    [rows, prefs.hideExcluded],
  );

  /* One place for every per-row edit, so the optimistic update and the error
     handling are not written out once per action. */
  async function patch(txn, fields) {
    setSaving(txn.id);
    try {
      await api.updateTransaction(txn.id, fields);
      // A category change also teaches the merchant cache, which can move rows
      // this view is filtering on - so the page is re-read rather than patched
      // in place, and every figure derived from it is dropped.
      invalidate('txns', 'analysis', 'dashboard', 'workflow');
      await refetch();
    } catch (e) {
      toast.fail('That change was not saved', e.message);
    } finally { setSaving(null); }
  }

  async function applyBulk(fields) {
    const ids = [...picked];
    if (!ids.length) return;
    setSaving('bulk');
    try {
      await api.bulkUpdate(ids, fields);
      setPicked(new Set());
      invalidate('txns', 'analysis', 'dashboard', 'workflow');
      await refetch();
      toast.ok(`${ids.length} transaction${ids.length === 1 ? '' : 's'} updated`);
    } catch (e) {
      toast.fail('The bulk change failed', e.message);
    } finally { setSaving(null); }
  }

  function exportCsv() {
    downloadCsv(`transactions-${today()}.csv`, toCsv(visible, [
      ['date', 'Date'],
      ['accounting_month', 'Counts in'],
      ['description', 'Description'],
      [(r) => accountName(r.account_id), 'Account'],
      ['category', 'Category'],
      ['flow_role', 'Counts as'],
      [(r) => (r.direction === 'credit' ? r.amount : -r.amount), 'Amount'],
      ['balance_after', 'Balance'],
      ['note', 'Note'],
    ]));
  }

  const rowHeight = prefs.density === 'compact' ? 56 : 76;
  const columns = 7 + (prefs.showRole ? 1 : 0) + (prefs.showBalance ? 1 : 0);
  const filtered = category || rail || search || dropped.size > 0;

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h2 className="h2">
            Ledger
            {total > 0 && <span className="section-note" style={{ marginLeft: 8 }}>
              {count(total)} rows{scoped ? ` · ${periodLabel}` : ''}
            </span>}
          </h2>
          <p className="lead">{active.hint}</p>
        </div>
        <Segmented
          value={view}
          onChange={setView}
          ariaLabel="Preset"
          options={VIEWS.map((v) => [v.key, v.label, v.hint])}
        />
      </div>

      {/* ---- filters ---- */}
      <Card pad>
        {accounts.length > 1 && (
          <div className="row tight" style={{ marginBottom: 10 }}>
            <span className="tiny dim" style={{ fontWeight: 600 }}>Accounts</span>
            <Button size="xs"
              onClick={() => setDropped(dropped.size ? new Set() : new Set(scopeIds))}>
              {dropped.size ? 'Select all' : 'Clear all'}
            </Button>
            {inScope.map((a) => (
              <Toggle
                key={a.id}
                on={!dropped.has(a.id)}
                title={a.display_name}
                onClick={() => setDropped((prev) => {
                  const next = new Set(prev);
                  if (next.has(a.id)) next.delete(a.id); else next.add(a.id);
                  return next;
                })}
              >
                {a.display_name}
              </Toggle>
            ))}
          </div>
        )}

        <div className="row">
          {!active.fixedCategory && (
            <Select
              value={category}
              onChange={setCategory}
              placeholder="All categories"
              options={categories.map((c) => [c, titleCase(c)])}
              aria-label="Category"
            />
          )}
          {active.rails && !active.fixedRail && (
            <Segmented
              value={rail}
              onChange={setRail}
              ariaLabel="Payment rail"
              options={[['', 'All'], ['upi', 'UPI'], ['non_upi', 'Other']]}
            />
          )}
          <Select value={sortBy} onChange={setSortBy} aria-label="Sort by"
            options={SORTS.map(([v, l]) => [v, `Sort: ${l}`])} />
          <IconButton
            icon={sortDir === 'asc' ? 'arrow-up' : 'arrow-down'}
            label={sortDir === 'asc' ? 'Ascending' : 'Descending'}
            onClick={() => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))}
          />
          <Search value={search} onChange={setSearch}
            placeholder="Search descriptions and merchants…"
            style={{ flex: 1, minWidth: 200 }} />
          <Button icon="download" onClick={exportCsv} disabled={!visible.length}
            title="Download what is currently shown, as CSV">
            Export
          </Button>
          {filtered && (
            <Button onClick={() => {
              if (!active.fixedCategory) setCategory('');
              if (!active.fixedRail) setRail('');
              setSearch('');
              setDropped(new Set());
            }}>
              Clear
            </Button>
          )}
          {fetching && !loading && <span className="spinner sm" />}
        </div>
      </Card>

      {error && <Callout tone="neg">{error.message}</Callout>}

      {/* ---- bulk bar ---- */}
      {picked.size > 0 && (
        <div className="card" style={{
          padding: 10, background: 'var(--accent-soft)', borderColor: 'var(--accent-border)',
        }}>
          <div className="row">
            <strong>{picked.size} selected</strong>
            <Select
              value=""
              placeholder="Set category…"
              onChange={(v) => v && applyBulk({ category: v })}
              options={categories.map((c) => [c, titleCase(c)])}
              disabled={saving === 'bulk'}
            />
            <Button size="sm" disabled={saving === 'bulk'}
              onClick={() => applyBulk({ excluded: true })}>Exclude from totals</Button>
            <Button size="sm" disabled={saving === 'bulk'}
              onClick={() => applyBulk({ excluded: false })}>Include</Button>
            <PromptButton size="sm" disabled={saving === 'bulk'}
              placeholder="Note for all selected"
              onSubmit={(note) => applyBulk({ note })}>
              Add note
            </PromptButton>
            <span className="spacer" />
            <Button size="sm" onClick={() => setPicked(new Set())}>Clear selection</Button>
          </div>
        </div>
      )}

      {/* ---- the table ---- */}
      {loading ? (
        <Loading label="Reading transactions…" />
      ) : !inScope.length && active.empty ? (
        <Empty title={active.empty} icon="rows">
          Import a statement, or scan your mailbox, to see them here.
        </Empty>
      ) : !visible.length ? (
        <Empty title={scoped ? `No transactions in ${periodLabel}` : 'No transactions match'}
          icon="search">
          {scoped
            ? 'Widen the period, or clear it, to see the rest of the ledger.'
            : 'Try a different account, or clear the filters.'}
        </Empty>
      ) : (
        <Card pad={false}>
          <div ref={scrollRef} style={{ maxHeight: '68vh', overflow: 'auto' }}>
            <table className={prefs.density === 'compact' ? 'tbl-compact' : ''}>
              <thead>
                <tr>
                  <th style={{ width: 30 }}>
                    <input
                      type="checkbox"
                      aria-label="Select every row on this page"
                      checked={visible.length > 0 && picked.size === visible.length}
                      onChange={(e) => setPicked(
                        e.target.checked ? new Set(visible.map((r) => r.id)) : new Set())}
                      style={{ accentColor: 'var(--accent)' }}
                    />
                  </th>
                  <th>Date</th>
                  <th>Description</th>
                  <th>Account</th>
                  <th>Category</th>
                  {prefs.showRole && <th>Counts as</th>}
                  <th className="right">Amount</th>
                  {prefs.showBalance && <th className="right">Balance</th>}
                  <th className="right">Edit</th>
                </tr>
              </thead>
              <VirtualBody
                count={visible.length}
                rowHeight={rowHeight}
                containerRef={scrollRef}
                columns={columns}
                // Explaining a row inserts a tall panel the fixed row height
                // cannot account for, so windowing steps aside while one is
                // open rather than mis-measuring the list.
                enabled={!explaining}
              >
                {(i) => (
                  <Row
                    key={visible[i].id}
                    t={visible[i]}
                    prefs={prefs}
                    categories={categories}
                    accountName={accountName}
                    saving={saving}
                    picked={picked}
                    onPick={(id) => setPicked((prev) => {
                      const next = new Set(prev);
                      if (next.has(id)) next.delete(id); else next.add(id);
                      return next;
                    })}
                    onPatch={patch}
                    explaining={explaining}
                    onExplain={(id) => setExplaining(explaining === id ? null : id)}
                    columns={columns}
                    toast={toast}
                    refetch={refetch}
                  />
                )}
              </VirtualBody>
            </table>
          </div>

          {pages > 1 && (
            <div className="row" style={{ padding: 12, borderTop: '1px solid var(--line)' }}>
              <Button size="sm" icon="chevron-left" disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}>Previous</Button>
              <span className="small muted">Page {page + 1} of {pages}</span>
              <Button size="sm" iconRight="chevron" disabled={page >= pages - 1}
                onClick={() => setPage((p) => p + 1)}>Next</Button>
              <span className="spacer" />
              <span className="tiny dim">{count(total)} rows in this filter</span>
            </div>
          )}
        </Card>
      )}

      <Callout>
        Changing a category also teaches the merchant permanently — every future
        statement with that merchant will use your choice instead of a rule or a model
        guess.
      </Callout>
    </>
  );
}

/* ── one row ─────────────────────────────────────────────────────────────── */

function Row({
  t, prefs, categories, accountName, saving, picked, onPick, onPatch,
  explaining, onExplain, columns, toast, refetch,
}) {
  return (
    <>
      <tr style={{ opacity: t.excluded ? 0.5 : 1 }}>
        <td>
          <input type="checkbox" aria-label={`Select ${t.description}`}
            checked={picked.has(t.id)} onChange={() => onPick(t.id)}
            style={{ accentColor: 'var(--accent)' }} />
        </td>
        <td className="nowrap">
          {dateLabel(t.date)}
          {t.accounting_month && t.accounting_month !== String(t.date).slice(0, 7) && (
            <div className="tiny dim" title="Counted in this month, not the month of the date">
              counts in {monthLabel(t.accounting_month)}
            </div>
          )}
        </td>
        <td>
          <div className="desc">{t.description}</div>
          <div className="row tight" style={{ marginTop: 3 }}>
            {t.is_internal_transfer && (
              <Chip tone="acc">{t.is_mirror_leg ? 'transfer (mirror)' : 'transfer'}</Chip>
            )}
            {t.excluded && <Chip tone="warn">excluded</Chip>}
            {t.needs_review && <Chip tone="warn">needs review</Chip>}
            {t.note && <span className="tiny dim">{t.note}</span>}
          </div>
        </td>
        <td className="nowrap tiny dim">{accountName(t.account_id)}</td>
        <td>
          <div className="row tight">
            <Select
              value={t.category}
              disabled={saving === t.id}
              onChange={(cat) => cat !== t.category && onPatch(t, { category: cat })}
              options={categories.map((c) => [c, titleCase(c)])}
              style={{ height: 26, fontSize: 12, maxWidth: 150 }}
            />
            {prefs.showSource && (
              <Chip tone={SOURCE_TONE[t.category_source]} title={whyCategorised(t)}>
                {SOURCE_LABEL[t.category_source] || t.category_source}
              </Chip>
            )}
          </div>
        </td>
        {prefs.showRole && (
          <td className="nowrap">
            <Chip>{titleCase((t.flow_role || 'unclassified').replace(/_/g, ' '))}</Chip>
          </td>
        )}
        <td className="right num nowrap" style={{
          color: t.direction === 'credit' ? 'var(--pos)' : 'inherit', fontWeight: 560,
        }}>
          {t.direction === 'credit' ? '+' : '−'}{money(t.amount, true)}
        </td>
        {prefs.showBalance && (
          <td className="right num nowrap dim">
            {t.balance_after != null ? money(t.balance_after) : '—'}
          </td>
        )}
        <td className="right nowrap">
          <PromptButton
            size="xs" className="ghost"
            title={t.note ? 'Edit note' : 'Add a note'}
            disabled={saving === t.id}
            initial={t.note || ''}
            placeholder="Note for this transaction"
            onSubmit={(note) => onPatch(t, { note })}
          >
            <Icon name={t.note ? 'edit' : 'plus'} size={12} />
          </PromptButton>
          <button
            className="btn ghost icon xs"
            title={t.excluded ? 'Put this back in your totals' : 'Leave this out of every total'}
            disabled={saving === t.id}
            onClick={() => onPatch(t, { excluded: !t.excluded })}
          >
            <Icon name={t.excluded ? 'refresh' : 'slash'} size={12} />
          </button>
          <button
            className="btn ghost icon xs"
            title="Why is this row the way it is?"
            aria-expanded={explaining === t.id}
            onClick={() => onExplain(t.id)}
          >
            <Icon name="question" size={12} />
          </button>
          {t.direction === 'debit' && !t.is_internal_transfer && (
            <PromptButton
              size="xs" className="ghost"
              title="This purchase was not mine — track it as owed to me"
              disabled={saving === t.id}
              placeholder="Whose expense was this?"
              submitLabel="Mark owed"
              onSubmit={async (who) => {
                if (!who) return;
                try {
                  await api.claimTransaction(t.id, {
                    counterparty: who, direction: 'owed_to_me', amount: t.amount,
                  });
                  await refetch();
                  toast.ok('Recorded as owed to you', 'It no longer counts as your spending.');
                } catch (e) { toast.fail('That could not be recorded', e.message); }
              }}
            >
              <Icon name="split" size={12} />
            </PromptButton>
          )}
        </td>
      </tr>
      {explaining === t.id && (
        <tr className="no-hover">
          <td colSpan={columns} style={{ padding: 0 }}>
            <Explanation id={t.id} />
          </td>
        </tr>
      )}
    </>
  );
}

/* Why this row has the category it has, in one sentence.
 *
 * "Categorised by a rule" is only half an answer - the half that does not help
 * somebody who disagrees with it. The categoriser has always known which of
 * its rules fired; it used to discard the label, and now records it. Rows
 * categorised before that change have the source but not the rule, and this
 * says so rather than claiming no rule matched, which would be a confident
 * wrong answer about the app's own reasoning. */
function whyCategorised(t) {
  if (t.category_rule) return `Matched the rule: ${t.category_rule}`;
  switch (t.category_source) {
    case 'transfer_match':
      return 'Set by pairing this with its other leg, not by a rule. The pairing is '
        + 'in the explanation below.';
    case 'rule':
      return 'A rule decided this, but which one was not recorded. Rebuilding the '
        + 'ledger captures it.';
    case 'merchant_cache':
      return 'Learned from a choice you made on this merchant before.';
    case 'llm': return 'Suggested by the model, not by a rule.';
    case 'user': return 'You set this.';
    default: return 'No rule matched — this is the fallback category.';
  }
}

/* What the app decided about one row, and on what evidence.
 *
 * Three questions that used to have one visible answer between them: what is
 * it, which way did the money go, and what is it part of. Each is read from
 * what was STORED at import time rather than recomputed - a recomputed answer
 * could differ from the one that produced the numbers on screen, and then this
 * panel would be explaining a ledger that does not exist. */
function Explanation({ id }) {
  const { data, error, loading } = useQuery(`explain:${id}`, () => api.explainTransaction(id));

  if (loading) return <div style={{ padding: 14 }}><Loading label="Reading what was recorded…" pad={0} /></div>;
  if (error) return <div style={{ padding: 14 }}><Callout tone="neg">{error.message}</Callout></div>;

  const c = data.category;
  const d = data.direction;
  const x = data.transfer;

  return (
    <div style={{
      padding: '14px 16px', background: 'var(--surface-2)',
      borderTop: '1px solid var(--line)', display: 'grid', gap: 16,
    }}>
      <div>
        <div className="rail-group-label" style={{ padding: '0 0 6px' }}>What it is</div>
        <div className="row tight">
          <Chip tone="acc">{titleCase(c.value)}</Chip>
          <Chip>{SOURCE_LABEL[c.source] || c.source}</Chip>
          {c.confidence > 0 && (
            <span className="tiny dim">{Math.round(c.confidence * 100)}% confident</span>
          )}
        </div>
        <div className="small muted" style={{ marginTop: 4, maxWidth: '70ch' }}>
          {c.rule ? (
            <>Matched the rule <strong>{c.rule}</strong>
              {c.pattern && <span title={c.pattern}> — hover for the full pattern</span>}.</>
          ) : c.source === 'transfer_match' ? (
            'Set by pairing this row with its other leg, not by a rule — see the pairing '
            + 'below. Nothing was forgotten; there is no rule to name.'
          ) : c.source === 'rule' ? (
            'A rule decided this, but which one was not recorded — the row was '
            + 'categorised before the app started keeping that. Rebuilding captures it.'
          ) : c.source === 'merchant_cache' ? (
            'Learned from a choice you made on this merchant before.'
          ) : c.source === 'user' ? 'You set this.' : 'No rule matched.'}
        </div>
      </div>

      {d && (
        <div>
          <div className="rail-group-label" style={{ padding: '0 0 6px' }}>
            Which way the money went
          </div>
          <div className="row tight">
            <Chip tone={d.value === 'credit' ? 'pos' : ''}>
              {d.value === 'credit' ? 'money in' : 'money out'}
            </Chip>
            {d.signal && <Chip>{d.signal}</Chip>}
          </div>
          {d.detail && (
            <div className="small muted" style={{ marginTop: 4, maxWidth: '70ch' }}>
              {d.detail}
            </div>
          )}
        </div>
      )}

      {x && (
        <div>
          <div className="rail-group-label" style={{ padding: '0 0 6px' }}>
            What it is part of
          </div>
          <div className="small muted" style={{ maxWidth: '70ch' }}>
            {x.note || x.kind}
            {x.counterpart && <> — paired with <strong>{x.counterpart}</strong></>}
          </div>
        </div>
      )}
    </div>
  );
}
