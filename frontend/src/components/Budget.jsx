import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, compact, count, dateLabel, money, pct, titleCase } from '../lib';
import { usePeriod } from '../period';
import { useDrill } from '../drill';
import { Callout, Card, Chip, Empty, Stat } from './ui';

/* What a month costs before any choices are made, and what that leaves.
 *
 * This tab exists because the questions people actually ask themselves are
 * not the ones a category breakdown answers:
 *
 *   Which of my expenses are fixed, and for how long will they stay fixed?
 *   Which vary, and by how much?
 *   What does a month cost me before I decide anything?
 *   What is left after that?
 *
 * Nothing on this page is a target anybody typed in. A commitment is a
 * recurring series found in the statements; its end date is the loan's own
 * amortization; a varying category's monthly figure is the MEDIAN of what it
 * actually cost per month - never the mean, because one holiday would
 * otherwise set the expectation for every month after it. See
 * backend/app/analytics/budget.py.
 */

const KIND_LABEL = {
  debt: 'Debt', spending: 'Fixed spending', saving: 'Committed saving',
};
const KIND_TONE = { debt: 'neg', spending: '', saving: 'accent' };
const KIND_NOTE = {
  debt: 'Buying down a balance. It has an end date, and that date is real.',
  spending: 'Leaves every month whether or not you decide anything.',
  saving: 'Committed, but still yours — so it is not counted as a cost.',
};

export default function Budget() {
  const { params, label, scoped, months: knownMonths, setPeriod } = usePeriod();
  const { drill } = useDrill();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const periodKey = JSON.stringify(params);

  const load = useCallback(() => {
    setData(null);
    api.budget(params)
      .then((body) => { setData(body); setError(null); })
      .catch((e) => { setError(e.message); setData(null); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [periodKey]);

  useEffect(load, [load]);

  const byKind = useMemo(() => {
    const out = { debt: [], spending: [], saving: [] };
    for (const c of data?.commitments || []) {
      (out[c.kind] || out.spending).push(c);
    }
    return out;
  }, [data]);

  if (error) return <Callout tone="neg">{error}</Callout>;
  if (!data) return <div className="spinner" style={{ margin: 40 }} />;
  if (data.status === 'empty') {
    return (
      <Empty title="Nothing to budget from yet">
        Import a statement, or scan your mailbox, and a month&apos;s shape
        follows from it.
      </Empty>
    );
  }

  const t = data.totals || {};
  const spoken = (t.committed_total || 0);
  const everyMonth = (data.variable || []).filter((v) => v.every_month);
  const occasional = (data.variable || []).filter((v) => !v.every_month);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>
          A typical month
          <span className="section-note">
            {scoped ? label : 'over your whole ledger'}
            {data.months ? ` · from ${data.months} month${data.months === 1 ? '' : 's'} of statements` : ''}
          </span>
        </h2>
        <p style={{ color: 'var(--text-2)', margin: 0, maxWidth: '78ch' }}>
          Every figure here is read off your own statements — a commitment is a
          charge that actually recurs, and a varying category&apos;s monthly
          figure is the middle month rather than the average, so one unusual
          month does not set the expectation for the rest.
        </p>
      </div>

      {/* ---- The four numbers the questions come down to ---- */}
      <div className="grid cols-4">
        <Stat
          label="A month brings in"
          value={t.income_typical}
          note="the middle month, not the average"
          tone="pos"
          onDrill={() => drill({
            title: 'Income',
            subtitle: 'Everything counted as money in over this period.',
            params: { flow_role: 'income' },
          })}
        />
        <Stat
          label="Spoken for"
          value={spoken}
          note={t.income_typical
            ? `${pct(t.committed_ratio)} of what comes in`
            : 'commitments found in your statements'}
        />
        <Stat
          label="A month costs"
          value={t.monthly_cost}
          note="commitments that leave for good, plus typical spending"
          tone="neg"
        />
        <Stat
          label="Left over"
          value={t.headroom}
          tone={(t.headroom || 0) >= 0 ? 'pos' : 'neg'}
          note={(t.headroom || 0) >= 0
            ? 'after everything above, saving included'
            : 'a typical month costs more than it brings in'}
        />
      </div>

      {/* The shape of the month in one bar. Four segments, each to scale, so
          "most of my income is already committed" is a thing you can see
          rather than a ratio you have to interpret. */}
      {t.income_typical > 0 && (
        <Card title="Where a month goes before you decide anything"
          sub={`${pct(t.committed_ratio)} committed`}>
          <div className="budget-bar" role="img"
            aria-label={`${pct(t.committed_ratio)} of a typical month is committed`}>
            {[
              ['debt', t.committed_debt, 'var(--c7)'],
              ['spending', t.committed_spending, 'var(--c3)'],
              ['saving', t.committed_saving, 'var(--c2)'],
              ['variable', Math.max(0, t.variable_typical || 0), 'var(--c6)'],
              ['left', Math.max(0, t.headroom || 0), 'var(--surface-2)'],
            ].map(([key, value, colour]) => {
              const share = (value || 0) / t.income_typical;
              if (share <= 0) return null;
              return (
                <span key={key} className="budget-seg"
                  title={`${key}: ${money(value)} (${(share * 100).toFixed(0)}%)`}
                  style={{ width: `${share * 100}%`, background: colour }} />
              );
            })}
          </div>
          <div className="legend">
            {[['Debt', 'var(--c7)', t.committed_debt],
              ['Fixed spending', 'var(--c3)', t.committed_spending],
              ['Committed saving', 'var(--c2)', t.committed_saving],
              ['Typical variable', 'var(--c6)', t.variable_typical],
              ['Left over', 'var(--border-strong)', t.headroom]].map(
              ([name, colour, value]) => (
                <span className="legend-item" key={name}>
                  <i className="dot" style={{ background: colour }} />
                  {name} <strong className="num">{compact(value)}</strong>
                </span>
              ))}
          </div>
        </Card>
      )}

      {(data.notes || []).map((n, i) => <Callout tone="warn" key={i}>{n}</Callout>)}

      {/* ---- Fixed ---- */}
      <div className="section-title">
        Fixed every month
        <span className="section-note">
          {data.commitments.length} commitment
          {data.commitments.length === 1 ? '' : 's'} found
        </span>
      </div>

      {!data.commitments.length ? (
        <Empty title="No commitments detected">
          A charge has to appear at least three times, at a steady interval and
          a stable amount, before it counts as a commitment. Import more months
          and the fixed part of your month emerges from them.
        </Empty>
      ) : (
        ['debt', 'spending', 'saving'].filter((k) => byKind[k].length).map((kind) => (
          <Card key={kind} title={KIND_LABEL[kind]} sub={KIND_NOTE[kind]}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>What</th>
                    <th>How often</th>
                    <th className="right">Per month</th>
                    <th className="right">Seen</th>
                    <th>Next</th>
                    <th>Until</th>
                  </tr>
                </thead>
                <tbody>
                  {byKind[kind].map((c) => (
                    <tr key={c.series_id} className="row-drill"
                      title={`Show every ${c.label} charge`}
                      onClick={() => drill({
                        title: c.label,
                        subtitle: `${money(c.monthly)} a month, ${c.cadence || 'recurring'}`
                          + `, seen ${c.occurrences} times in your ledger`
                          + (c.account ? ` on ${c.account}` : '')
                          + '.',
                        params: { category: c.category },
                      })}>
                      <td>
                        <div style={{ fontWeight: 550 }}>{c.label}</div>
                        <div style={{ display: 'flex', gap: 6, marginTop: 3,
                          flexWrap: 'wrap' }}>
                          <Chip>{titleCase(c.category)}</Chip>
                          {c.account && (
                            <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                              {c.account}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="nowrap">{c.cadence || `${c.cadence_days}d`}</td>
                      <td className="right num nowrap">{money(c.monthly)}</td>
                      <td className="right num nowrap">
                        {c.months_seen}/{data.months}
                        {c.months_seen < 2 && (
                          <div style={{ fontSize: 10.5, color: 'var(--warn)' }}>
                            once here
                          </div>
                        )}
                      </td>
                      <td className="nowrap" style={{ color: 'var(--text-2)' }}>
                        {c.next_expected ? dateLabel(c.next_expected) : '—'}
                      </td>
                      <td className="nowrap">
                        {c.ends_on ? (
                          <>
                            <div>{dateLabel(c.ends_on)}</div>
                            <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                              {c.months_left} more payment
                              {c.months_left === 1 ? '' : 's'}
                            </div>
                          </>
                        ) : (
                          <span style={{ color: 'var(--text-3)' }}>
                            {kind === 'debt' ? 'no schedule found' : 'until you stop it'}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ))
      )}

      {/* ---- Varies ---- */}
      <div className="section-title">
        Varies month to month
        <span className="section-note">
          typical is the middle month, not the average
        </span>
      </div>

      {!data.variable.length ? (
        <Empty title="Nothing variable in this period">
          Every rupee that left is accounted for by a commitment above.
        </Empty>
      ) : (
        <>
          {everyMonth.length > 0 && (
            <VariableTable
              title="Every month, in varying amounts"
              sub="No single merchant recurs, but the category always does — so
                   this is effectively fixed, with a range instead of a figure."
              rows={everyMonth} months={data.months} drill={drill} />
          )}
          {occasional.length > 0 && (
            <VariableTable
              title="Some months"
              sub="Present in some months and not others. The typical figure is
                   the middle month it appeared in, not spread across the whole
                   period."
              rows={occasional} months={data.months} drill={drill} />
          )}
        </>
      )}

      {knownMonths.length > 1 && (
        <Callout>
          A typical month is only as good as the months behind it. This one is
          built from {data.months} of them
          {scoped && (
            <>
              {' '}— <button className="btn link"
                onClick={() => setPeriod({ preset: 'all' })}>
                use every month on record
              </button> for a steadier figure
            </>
          )}.
        </Callout>
      )}
    </div>
  );
}

function VariableTable({ title, sub, rows, months, drill }) {
  /* The sum of each category's middle month - which is NOT the same as the
     middle month's total, and is the larger of the two, because categories
     peak in different months. The headline "typical variable" figure is the
     middle month's total; this column adds up, so both are stated plainly
     rather than left to look like an arithmetic error. */
  const total = rows.reduce((sum, r) => sum + (r.typical_monthly || 0), 0);
  return (
    <Card title={title} sub={sub}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Category</th>
              <th className="right">Typical month</th>
              <th className="right">Quietest</th>
              <th className="right">Worst</th>
              <th className="right">Months</th>
              <th className="right">Rows</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <tr key={v.category} className="row-drill"
                title={`Show every ${titleCase(v.category)} transaction`}
                onClick={() => drill({
                  title: titleCase(v.category),
                  subtitle: `Typically ${money(v.typical_monthly)} a month, `
                    + `between ${money(v.low_monthly)} and `
                    + `${money(v.high_monthly)}. ${money(v.total)} in total `
                    + `over ${v.months_seen} month${v.months_seen === 1 ? '' : 's'}.`,
                  params: { category: v.category },
                })}>
                <td>
                  {titleCase(v.category)}
                  {v.typical_monthly < 0 && (
                    <Chip tone="pos" style={{ marginLeft: 6 }}>money back</Chip>
                  )}
                  <div style={{ fontSize: 11, color: 'var(--text-3)' }}>{v.group}</div>
                </td>
                <td className="right num nowrap" style={{ fontWeight: 550 }}>
                  {money(v.typical_monthly)}
                </td>
                <td className="right num nowrap" style={{ color: 'var(--text-3)' }}>
                  {money(v.low_monthly)}
                </td>
                <td className="right num nowrap" style={{ color: 'var(--text-3)' }}>
                  {money(v.high_monthly)}
                </td>
                <td className="right num nowrap">{v.months_seen}/{months}</td>
                <td className="right num nowrap">{count(v.count)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th>These, added up</th>
              <th className="right num">{money(total)}</th>
              <th colSpan={4} />
            </tr>
          </tfoot>
        </table>
      </div>
      <p style={{ fontSize: 11.5, color: 'var(--text-3)', margin: '8px 0 0',
        lineHeight: 1.55 }}>
        Each figure is that category&rsquo;s middle month over the period.
        Adding them describes a month where every category peaked at once,
        so it comes to more than the typical month at the top of this tab —
        which is the middle month of the whole variable spend.
      </p>
    </Card>
  );
}
