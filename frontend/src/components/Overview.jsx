import React from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, ComposedChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { colorFor, compact, count, dateLabel, money, monthLabel, pct,
  SPEND_ROLES, titleCase } from '../lib';
import { BarList, Callout, Card, ChartTooltip, Chip, Stat, axisProps, moneyAxis } from './ui';
import Claims from './Claims';
import Forecast from './Forecast';
import { usePeriod } from '../period';
import { useDrill } from '../drill';

const SEVERITY_TONE = { urgent: 'neg', watch: 'warn', info: 'accent' };

export default function Overview({ data }) {
  const { analysis, narrative, forecast, transfers, data_quality: quality } = data;
  const totals = analysis.totals || {};
  const period = analysis.period || {};
  /* Which window these figures are for. The numbers themselves were computed
     server-side for it (see /api/analysis); this is how the page says so. */
  const { label: periodLabel, scoped, window: resolved } = usePeriod();
  const { drill } = useDrill();

  /* Which categories belong to a group, read off the breakdown the server
     sent rather than duplicating CATEGORY_GROUPS here - the payload already
     names each category's group, so this cannot drift from it. */
  const categoriesInGroup = (group) => (analysis.by_category || [])
    .filter((c) => c.group === group).map((c) => c.category);

  const monthly = (analysis.monthly || []).map((m) => ({
    ...m, label: monthLabel(m.month),
  }));

  const categories = (analysis.by_category || []).map((c, i) => ({
    label: c.category, value: c.total, color: colorFor(i), ...c,
  }));

  const netWorth = analysis.net_worth || {};
  const position = analysis.position || {};

  return (
    <>
      {/* ---- Narrative ----

          Written about the WHOLE ledger, once, when the statements were
          parsed. Every figure below can be recomputed for a period; prose
          cannot, and re-titling last quarter's summary as if it described
          this month would be exactly the kind of plausible-and-wrong output
          this app exists to avoid. So it is labelled rather than hidden: the
          summary is still true, just not about this window. */}
      {narrative?.headline && (
        <Card className="grid" style={{ gap: 12 }}>
          <div>
            <h2 className="headline">{narrative.headline}</h2>
            <div className="prose"><p>{narrative.summary}</p></div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {scoped && (
                <Chip tone="warn" title="Prose is written once, per import">
                  Describes your whole ledger, not {periodLabel}
                </Chip>
              )}
              {narrative.generated_by === 'computed' && (
                <Chip tone="warn">Computed summary — no model narration</Chip>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* ---- Headline numbers ---- */}
      <div className="section-title">
        {scoped ? periodLabel : 'Summary'}
        {period.start && period.end && (
          <span className="section-note">
            {/* The real first and last dates of the rows that counted, which
                for an accounting month is not the month boundary: August's
                rows can run from 27 July to 1 September. */}
            {dateLabel(period.start)} → {dateLabel(period.end)}
            {' · '}
            {period.months_covered} month{period.months_covered === 1 ? '' : 's'}
            {scoped && resolved?.basis === 'accounting' && ' · by accounting month'}
          </span>
        )}
      </div>
      <div className="grid cols-4">
        <Stat
          label="Money in"
          value={totals.income}
          note={`${compact(totals.average_monthly_income)} average per month`}
          onDrill={() => drill({
            title: 'Money in',
            subtitle: 'Everything counted as income in this period — pay, '
              + 'interest and anything else that genuinely came in. Refunds '
              + 'and repayments are counted against spending instead, so they '
              + 'are not here.',
            params: { flow_role: 'income' },
          })}
        />
        <Stat
          label="Money out"
          value={totals.spend}
          note={`${compact(totals.average_monthly_spend)} average per month`}
          onDrill={() => drill({
            title: 'Money out',
            subtitle: 'Spending, net of anything that came back against it. '
              + 'EMIs and SIPs are money leaving too, but they are commitments '
              + 'and investments rather than spending — they have their own '
              + 'tiles.',
            params: { flow_role: 'expense,refund,claim_settlement' },
          })}
        />
        <Stat
          label="Net saved"
          value={totals.net_savings}
          tone={totals.net_savings >= 0 ? 'pos' : 'neg'}
          note={`Savings rate ${pct(totals.savings_rate)}`}
        />
        <Stat
          label="Invested"
          value={totals.invested}
          note={`${count(totals.transaction_count)} transactions analyzed`}
          onDrill={() => drill({
            title: 'Invested',
            subtitle: 'Money moved into investments. Still yours, so it counts '
              + 'as saved rather than spent.',
            params: { flow_role: 'investment' },
          })}
        />
      </div>

      {/* ---- Cashflow ---- */}
      <div className="section-title">Cashflow</div>
      <div className="grid cols-2">
        <Card title="Income vs outflow by month" sub="Outflow includes EMIs and SIPs">
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={monthly} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis {...moneyAxis} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'var(--surface-2)' }} />
              <Bar dataKey="income" name="Income" fill="var(--c2)" radius={[3, 3, 0, 0]} maxBarSize={26} />
              <Bar dataKey="total_outflow" name="Outflow" fill="var(--c7)" radius={[3, 3, 0, 0]} maxBarSize={26} />
              <Line dataKey="net" name="Net" stroke="var(--c1)" strokeWidth={2} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
          {/* The chart's own months, as one row of buttons. Recharts can
              report a click on a bar, but a bar is a small target and a
              stacked one is ambiguous about which series was hit - a strip
              under the axis is unambiguous and reachable by keyboard. */}
          {monthly.length > 1 && (
            <div className="month-strip" style={{ marginTop: 10 }}>
              {[...monthly].reverse().map((m) => (
                <button key={m.month} className="chip-toggle"
                  title={`Every transaction counted in ${m.label}`}
                  onClick={() => drill({
                    title: m.label,
                    subtitle: `${money(m.income)} in, ${money(m.spend)} out, `
                      + `${money(m.net)} net — everything counted in this month.`,
                    ignorePeriod: true,
                    periodLabel: m.label,
                    sortBy: 'date',
                    params: { start_month: m.month, end_month: m.month },
                  })}>
                  {m.label}
                </button>
              ))}
            </div>
          )}
          <div className="legend">
            <span className="legend-item"><i className="dot" style={{ background: 'var(--c2)' }} />Income</span>
            <span className="legend-item"><i className="dot" style={{ background: 'var(--c7)' }} />Outflow</span>
            <span className="legend-item"><i className="dot" style={{ background: 'var(--c1)' }} />Net</span>
          </div>
        </Card>

        <Card title="Where the money went"
          sub={`${categories.length} categories — click one for the rows`}>
          <BarList
            items={categories} total={totals.spend} max={11}
            onPick={(item) => drill({
              title: titleCase(item.label),
              subtitle: `${money(item.total)} across ${item.count} `
                + `transaction${item.count === 1 ? '' : 's'}`
                + (item.monthly_average
                  ? `, averaging ${money(item.monthly_average)} a month.` : '.'),
              params: { category: item.category, flow_role: SPEND_ROLES },
            })}
          />
          {/* The bars are what went OUT. "Money out" above is what went out
              after money that came back is deducted, so without this line the
              two disagree by exactly the refunds and nothing on screen says
              why - in June that was 63,736 of a 130,241 gross, and the card
              simply looked wrong. */}
          {totals.offsets > 0 && (
            <div className="section-note" style={{ marginTop: 10 }}>
              {money(totals.gross_spend)} went out and {money(totals.offsets)}
              {' '}came back as refunds and reimbursements, which is the
              {' '}{money(totals.spend)} counted above. The bars show what went out.
            </div>
          )}
        </Card>
      </div>

      {/* ---- Narrative findings ---- */}
      {narrative?.key_findings?.length > 0 && (
        <>
          <div className="section-title">
            What stands out
            {scoped && <span className="section-note">whole ledger</span>}
          </div>
          <Card>
            {narrative.key_findings.map((f, i) => (
              <div className="finding" key={i}>
                <Chip tone={SEVERITY_TONE[f.severity] || 'accent'}>
                  {f.severity || 'info'}
                </Chip>
                <div className="finding-body">
                  <div className="finding-title">{f.title}</div>
                  <div className="finding-detail">{f.detail}</div>
                </div>
              </div>
            ))}
          </Card>
        </>
      )}

      {narrative?.where_money_went && (
        <>
          <div className="section-title">
            Following the salary
            {scoped && <span className="section-note">whole ledger</span>}
          </div>
          <Card><div className="prose"><p>{narrative.where_money_went}</p></div></Card>
        </>
      )}

      {narrative?.observations?.length > 0 && (
        <>
          <div className="section-title">
            Options worth knowing about
            {scoped && <span className="section-note">whole ledger</span>}
          </div>
          <Card>
            {narrative.observations.map((o, i) => (
              <div className="finding" key={i}>
                <span className="dot" style={{ background: colorFor(i), marginTop: 6 }} />
                <div className="finding-body">
                  <div className="finding-title">{o.title}</div>
                  <div className="finding-detail">{o.detail}</div>
                  {o.mechanism && <div className="finding-mech">{o.mechanism}</div>}
                </div>
              </div>
            ))}
            <Callout>
              These are mechanical observations from your own numbers, not financial
              advice. Anything involving investing, prepaying or restructuring debt is
              worth discussing with a qualified adviser who can see your full picture.
            </Callout>
          </Card>
        </>
      )}

      {/* ---- Position ----

          As at the END of the window, read from the balance each statement
          printed after its last row up to that date - not "latest known",
          which was a figure that refused to move however far back you looked.
          See engine._net_worth_as_at. */}
      <div className="section-title">
        Position
        <span className="section-note">
          {position.basis === 'period'
            ? `as at ${dateLabel(position.as_of)}`
            /* Not "as at the end of the window": nothing in it printed a
               balance, so this is the latest figure there is, and saying
               which it is beats a figure that looks like it followed the
               period and did not. */
            : position.as_of
              ? `latest known balances, as at ${dateLabel(position.as_of)}`
                + (scoped ? ` — not ${periodLabel}` : '')
              : `latest known balances${scoped ? ` — not ${periodLabel}` : ''}`}
        </span>
      </div>
      <div className="grid cols-3">
        <Stat
          label="Assets tracked"
          value={netWorth._assets}
          note="Cash balances, as printed on the statements"
        />
        <Stat
          label="Liabilities"
          value={netWorth._liabilities}
          tone="neg"
          note="Loans and card dues outstanding"
        />
        <Stat
          label="Net position"
          value={netWorth._net}
          tone={netWorth._net >= 0 ? 'pos' : 'neg'}
          note="Assets minus liabilities on tracked accounts"
        />
      </div>
      {position.missing?.length > 0 && position.basis === 'period' && (
        <Callout tone="warn">
          {/* Named rather than counted: a total quietly missing an account is
              worse than one that says which account it is missing. */}
          No balance could be established as at {dateLabel(position.as_of)} for{' '}
          {position.missing.join(', ')} — {position.missing.length === 1
            ? 'it is' : 'they are'} left out of the figures above. Card
          statements often print no running balance.
        </Callout>
      )}

      {/* ---- Data quality ----

          Two kinds of figure here, and they are labelled apart. "Uncategorised"
          is a property of the rows in this window and follows it. Files and
          transfer matching are properties of what has been imported, which is
          not a period at all - a file does not belong to March. */}
      <div className="section-title">
        Data quality
        {scoped && <span className="section-note">whole ledger, except where noted</span>}
      </div>
      <Card>
        <div className="grid cols-4" style={{ gap: 10, marginBottom: 12 }}>
          <QualityTile label="Files reconciled" value={`${quality?.files_reconciled ?? 0}/${quality?.files_processed ?? 0}`} tone={quality?.files_unreconciled ? 'warn' : 'pos'} />
          <QualityTile label="Rules-categorized" value={quality?.rules_settled ?? 0} />
          <QualityTile
            label={scoped ? `Uncategorised in ${periodLabel}` : 'Uncategorised'}
            value={count(analysis.uncategorized?.count ?? 0)}
            tone={analysis.uncategorized?.count ? 'warn' : 'pos'}
            onDrill={analysis.uncategorized?.count
              ? () => drill({
                title: 'Uncategorised',
                subtitle: 'No rule matched these, so they sit outside the '
                  + 'category breakdown.',
                params: { category: 'uncategorized' },
              })
              : undefined}
          />
          <QualityTile label="Double-count avoided" value={compact(transfers?.double_count_avoided || 0)} tone="pos" />
        </div>

        {transfers?.notes?.map((n, i) => <Callout tone="pos" key={i}>{n}</Callout>)}
        {(narrative?.caveats || []).map((c, i) => <Callout tone="warn" key={`c${i}`}>{c}</Callout>)}
        {(analysis.notes || []).map((n, i) => <Callout key={`n${i}`}>{n}</Callout>)}
      </Card>

      {/* Forecast and Owed were tabs of their own. Both answer a question about
          the position this page already describes - what happens next, and what
          is coming back - so they read better as the end of that story than as
          two more places to go looking. */}
      <div className="section-title">
        What happens next
        {scoped && <span className="section-note">from today, whatever the period</span>}
      </div>
      <Forecast data={data} />

      <div className="section-title">
        Money owed to you
        {scoped && <span className="section-note">everything outstanding</span>}
      </div>
      <Claims />
    </>
  );
}

function QualityTile({ label, value, tone, onDrill }) {
  return (
    <div style={{
      padding: '10px 12px', borderRadius: 8,
      background: 'var(--surface-2)', border: '1px solid var(--border)',
    }}>
      <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginBottom: 3 }}>{label}</div>
      <div className={`num ${tone === 'pos' ? 'stat-value pos' : tone === 'warn' ? 'stat-value neg' : ''}`}
        style={{ fontSize: 18, fontWeight: 620 }}>
        {onDrill ? (
          <button type="button" className="drill-link" onClick={onDrill}
            title="Show these transactions">{value}</button>
        ) : value}
      </div>
    </div>
  );
}
