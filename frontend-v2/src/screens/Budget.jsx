/* What a month costs before any choices are made, and what that leaves.
 *
 * This screen exists because the questions people actually ask themselves are
 * not the ones a category breakdown answers:
 *
 *   Which of my expenses are fixed, and for how long will they stay fixed?
 *   Which vary, and by how much?
 *   What does a month cost me before I decide anything?
 *   What is left after that?
 *
 * Nothing here is a target anybody typed in. A commitment is a recurring
 * series found in the statements; its end date is the loan's own amortization;
 * a varying category's monthly figure is the MEDIAN of what it actually cost
 * per month - never the mean, because one holiday would otherwise set the
 * expectation for every month after it.
 */

import React, { useMemo } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { usePeriod } from '../core/period';
import { useDrill } from '../core/drill';
import { compact, count, dateLabel, money, pct, titleCase } from '../core/format';
import {
  Button, Callout, Card, Chip, Empty, Legend, Loading, Section, Stat, Table,
} from '../ui';
import { StackBar } from '../ui/charts';

const KIND = {
  debt: {
    label: 'Debt',
    note: 'Buying down a balance. It has an end date, and that date is real.',
  },
  spending: {
    label: 'Fixed spending',
    note: 'Leaves every month whether or not you decide anything.',
  },
  saving: {
    label: 'Committed saving',
    note: 'Committed, but still yours — so it is not counted as a cost.',
  },
};

export default function Budget() {
  const { params, paramsKey, label, scoped, months: knownMonths, setPeriod } = usePeriod();
  const { drill } = useDrill();
  const { data, loading, error } = useQuery(`budget:${paramsKey}`, () => api.budget(params));

  const byKind = useMemo(() => {
    const out = { debt: [], spending: [], saving: [] };
    for (const c of data?.commitments || []) (out[c.kind] || out.spending).push(c);
    return out;
  }, [data]);

  if (error) return <Callout tone="neg">{error.message}</Callout>;
  if (loading) return <Loading label="Working out a typical month…" />;
  if (data?.status === 'empty') {
    return (
      <Empty title="Nothing to budget from yet" icon="wallet">
        Import a statement, or scan your mailbox, and a month&apos;s shape follows from it.
      </Empty>
    );
  }

  const t = data.totals || {};
  const committed = t.committed_total || 0;
  const everyMonth = (data.variable || []).filter((v) => v.every_month);
  const occasional = (data.variable || []).filter((v) => !v.every_month);

  return (
    <>
      <div>
        <h2 className="h2">
          A typical month
          <span className="section-note" style={{ marginLeft: 10 }}>
            {scoped ? label : 'over your whole ledger'}
            {data.months
              ? ` · from ${data.months} month${data.months === 1 ? '' : 's'} of statements`
              : ''}
          </span>
        </h2>
        <p className="lead">
          Every figure here is read off your own statements — a commitment is a charge
          that actually recurs, and a varying category&apos;s monthly figure is the
          middle month rather than the average, so one unusual month does not set the
          expectation for the rest.
        </p>
      </div>

      <div className="grid cols-4">
        <Stat
          label="A month brings in" value={t.income_typical} tone="pos"
          note="the middle month, not the average"
          onDrill={() => drill({
            title: 'Income',
            subtitle: 'Everything counted as money in over this period.',
            params: { flow_role: 'income' },
          })}
        />
        <Stat
          label="Spoken for" value={committed}
          note={t.income_typical ? `${pct(t.committed_ratio)} of what comes in`
            : 'commitments found in your statements'}
        />
        <Stat
          label="A month costs" value={t.monthly_cost} tone="neg"
          note="commitments that leave for good, plus typical spending"
        />
        <Stat
          label="Left over" value={t.headroom}
          tone={(t.headroom || 0) >= 0 ? 'pos' : 'neg'}
          note={(t.headroom || 0) >= 0
            ? 'after everything above, saving included'
            : 'a typical month costs more than it brings in'}
        />
      </div>

      {/* The shape of the month in one bar, each segment to scale, so "most of
          my income is already committed" is a thing you can see rather than a
          ratio you have to interpret. */}
      {t.income_typical > 0 && (
        <Card title="Where a month goes before you decide anything"
          sub={`${pct(t.committed_ratio)} committed`}>
          <StackBar
            height={16}
            total={t.income_typical}
            segments={[
              { label: 'Debt', value: t.committed_debt, color: 'var(--c7)' },
              { label: 'Fixed spending', value: t.committed_spending, color: 'var(--c3)' },
              { label: 'Committed saving', value: t.committed_saving, color: 'var(--c2)' },
              { label: 'Typical variable', value: Math.max(0, t.variable_typical || 0), color: 'var(--c6)' },
              { label: 'Left over', value: Math.max(0, t.headroom || 0), color: 'var(--surface-3)' },
            ]}
          />
          <Legend items={[
            { label: 'Debt', color: 'var(--c7)', value: compact(t.committed_debt) },
            { label: 'Fixed spending', color: 'var(--c3)', value: compact(t.committed_spending) },
            { label: 'Committed saving', color: 'var(--c2)', value: compact(t.committed_saving) },
            { label: 'Typical variable', color: 'var(--c6)', value: compact(t.variable_typical) },
            { label: 'Left over', color: 'var(--line-strong)', value: compact(t.headroom) },
          ]} />
        </Card>
      )}

      {(data.notes || []).map((n, i) => <Callout tone="warn" key={i}>{n}</Callout>)}

      {/* ---- fixed ---- */}
      <Section
        title="Fixed every month"
        note={`${data.commitments.length} commitment${data.commitments.length === 1 ? '' : 's'} found`}
      />

      {!data.commitments.length ? (
        <Empty title="No commitments detected" icon="repeat">
          A charge has to appear at least three times, at a steady interval and a stable
          amount, before it counts as a commitment. Import more months and the fixed
          part of your month emerges from them.
        </Empty>
      ) : (
        ['debt', 'spending', 'saving'].filter((k) => byKind[k].length).map((kind) => (
          <Card key={kind} title={KIND[kind].label} sub={KIND[kind].note}>
            <Table>
              <thead>
                <tr>
                  <th>What</th><th>How often</th>
                  <th className="right">Per month</th><th className="right">Seen</th>
                  <th>Next</th><th>Until</th>
                </tr>
              </thead>
              <tbody>
                {byKind[kind].map((c) => (
                  <tr key={c.series_id} className="row-click"
                    title={`Show every ${c.label} charge`}
                    onClick={() => drill({
                      title: c.label,
                      subtitle: `${money(c.monthly)} a month, ${c.cadence || 'recurring'}, `
                        + `seen ${c.occurrences} times in your ledger`
                        + (c.account ? ` on ${c.account}` : '') + '.',
                      params: { category: c.category },
                    })}>
                    <td>
                      <div style={{ fontWeight: 560 }}>{c.label}</div>
                      <div className="row tight" style={{ marginTop: 3 }}>
                        <Chip>{titleCase(c.category)}</Chip>
                        {c.account && <span className="tiny dim">{c.account}</span>}
                      </div>
                    </td>
                    <td className="nowrap">{c.cadence || `${c.cadence_days}d`}</td>
                    <td className="right num nowrap">{money(c.monthly)}</td>
                    <td className="right num nowrap">
                      {c.months_seen}/{data.months}
                      {c.months_seen < 2 && <div className="tiny warnc">once here</div>}
                    </td>
                    <td className="nowrap muted">
                      {c.next_expected ? dateLabel(c.next_expected) : '—'}
                    </td>
                    <td className="nowrap">
                      {c.ends_on ? (
                        <>
                          <div>{dateLabel(c.ends_on)}</div>
                          <div className="tiny dim">
                            {c.months_left} more payment{c.months_left === 1 ? '' : 's'}
                          </div>
                        </>
                      ) : (
                        <span className="dim">
                          {kind === 'debt' ? 'no schedule found' : 'until you stop it'}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        ))
      )}

      {/* ---- varies ---- */}
      <Section title="Varies month to month"
        note="typical is the middle month, not the average" />

      {!data.variable.length ? (
        <Empty title="Nothing variable in this period" icon="trending">
          Every rupee that left is accounted for by a commitment above.
        </Empty>
      ) : (
        <>
          {everyMonth.length > 0 && (
            <VariableTable
              title="Every month, in varying amounts"
              sub="No single merchant recurs, but the category always does — so this is
                   effectively fixed, with a range instead of a figure."
              rows={everyMonth} months={data.months} drill={drill}
            />
          )}
          {occasional.length > 0 && (
            <VariableTable
              title="Some months"
              sub="Present in some months and not others. The typical figure is the middle
                   month it appeared in, not spread across the whole period."
              rows={occasional} months={data.months} drill={drill}
            />
          )}
        </>
      )}

      {knownMonths.length > 1 && (
        <Callout>
          A typical month is only as good as the months behind it. This one is built
          from {data.months} of them
          {scoped && (
            <>
              {' '}—{' '}
              <Button variant="link" onClick={() => setPeriod({ preset: 'all' })}>
                use every month on record
              </Button>
              {' '}for a steadier figure
            </>
          )}.
        </Callout>
      )}
    </>
  );
}

function VariableTable({ title, sub, rows, months, drill }) {
  /* The sum of each category's middle month - which is NOT the same as the
     middle month's total, and is the larger of the two, because categories
     peak in different months. The headline "typical variable" figure is the
     middle month's total; this column adds up, so both are stated plainly
     rather than left to look like an arithmetic error. */
  const total = rows.reduce((s, r) => s + (r.typical_monthly || 0), 0);
  return (
    <Card title={title} sub={sub}>
      <Table>
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
            <tr key={v.category} className="row-click"
              title={`Show every ${titleCase(v.category)} transaction`}
              onClick={() => drill({
                title: titleCase(v.category),
                subtitle: `Typically ${money(v.typical_monthly)} a month, between `
                  + `${money(v.low_monthly)} and ${money(v.high_monthly)}. `
                  + `${money(v.total)} in total over ${v.months_seen} `
                  + `month${v.months_seen === 1 ? '' : 's'}.`,
                params: { category: v.category },
              })}>
              <td>
                {titleCase(v.category)}
                {v.typical_monthly < 0 && (
                  <Chip tone="pos" style={{ marginLeft: 6 }}>money back</Chip>
                )}
                <div className="tiny dim">{v.group}</div>
              </td>
              <td className="right num nowrap" style={{ fontWeight: 560 }}>
                {money(v.typical_monthly)}
              </td>
              <td className="right num nowrap dim">{money(v.low_monthly)}</td>
              <td className="right num nowrap dim">{money(v.high_monthly)}</td>
              <td className="right num nowrap">{v.months_seen}/{months}</td>
              <td className="right num nowrap">{count(v.count)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td>These, added up</td>
            <td className="right num">{money(total)}</td>
            <td colSpan={4} />
          </tr>
        </tfoot>
      </Table>
      <p className="tiny dim" style={{ margin: '8px 0 0', lineHeight: 1.6 }}>
        Each figure is that category&rsquo;s middle month over the period. Adding them
        describes a month where every category peaked at once, so it comes to more than
        the typical month at the top of this screen — which is the middle month of the
        whole variable spend.
      </p>
    </Card>
  );
}
