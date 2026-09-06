/* Overview: the whole picture, for the period on screen.
 *
 * The order is an argument about what somebody actually wants to know, in
 * order: what came in and what went out, where it went, what the model noticed,
 * what you are worth, and how much of the above can be trusted.
 *
 * Every prose block on this page is labelled when a period is set. The
 * narrative is written once, about the whole ledger, when the statements are
 * parsed; re-titling last quarter's summary as if it described this month
 * would be exactly the kind of plausible-and-wrong output this app exists to
 * avoid.
 */

import React from 'react';
import { Link } from '../core/router';
import { usePeriod } from '../core/period';
import { useDrill } from '../core/drill';
import { useViewData } from '../core/ledger';
import { usePrefs } from '../core/prefs';
import {
  colorFor, compact, count, dateLabel, money, monthLabel, pct, SPEND_ROLES, titleCase,
} from '../core/format';
import {
  BarList, Button, Callout, Card, Chip, Empty, Icon, Legend, Section, Skeleton,
  SkeletonStats, Stat,
} from '../ui';
import { ComboChart, useChartKeyframes } from '../ui/charts';
import { PeriodEmpty } from '../app/PeriodBar';

const SEVERITY = { urgent: 'neg', watch: 'warn', info: 'acc' };

export default function Overview({ onImport }) {
  useChartKeyframes();
  const { data, loading, windowEmpty, error } = useViewData();
  const { label: periodLabel, scoped, window: resolved } = usePeriod();
  const { drill } = useDrill();
  const [prefs] = usePrefs();

  if (loading) {
    return (
      <>
        <SkeletonStats />
        <div className="grid cols-2">
          <div className="card"><div className="card-body"><Skeleton lines={8} /></div></div>
          <div className="card"><div className="card-body"><Skeleton lines={8} /></div></div>
        </div>
      </>
    );
  }
  if (error && !data) return <Callout tone="neg">{error.message}</Callout>;
  if (!data) {
    return (
      <Empty title="Nothing analysed yet" icon="inbox"
        action={<Button variant="primary" icon="upload" onClick={onImport}>Import statements</Button>}>
        Import a statement, or scan your mailbox, and everything on this screen follows
        from it.
      </Empty>
    );
  }
  if (windowEmpty) return <PeriodEmpty available={data.available} />;

  const { analysis, narrative, transfers, data_quality: quality } = data;
  const totals = analysis.totals || {};
  const period = analysis.period || {};
  const netWorth = analysis.net_worth || {};
  const position = analysis.position || {};

  const monthly = (analysis.monthly || []).map((m) => ({ ...m, label: monthLabel(m.month) }));
  const categories = (analysis.by_category || []).map((c, i) => ({
    ...c, label: c.category, value: c.total, color: colorFor(i),
  }));

  return (
    <>
      {/* ---- narrative ---- */}
      {narrative?.headline && (
        <Card pad>
          <h2 className="h1" style={{ marginBottom: 8 }}>{narrative.headline}</h2>
          <div className="prose"><p>{narrative.summary}</p></div>
          <div className="row tight" style={{ marginTop: 10 }}>
            {scoped && (
              <Chip tone="warn" title="Prose is written once, per import">
                Describes your whole ledger, not {periodLabel}
              </Chip>
            )}
            {narrative.generated_by === 'computed' && (
              <Chip tone="warn">Computed summary — no model narration</Chip>
            )}
          </div>
        </Card>
      )}

      {/* ---- headline numbers ---- */}
      <Section
        title={scoped ? periodLabel : 'Summary'}
        note={period.start && period.end
          /* The real first and last dates of the rows that counted, which for
             an accounting month is not the month boundary: August's rows can
             run from 27 July to 1 September. */
          ? `${dateLabel(period.start)} → ${dateLabel(period.end)} · `
            + `${period.months_covered} month${period.months_covered === 1 ? '' : 's'}`
            + (scoped && resolved?.basis === 'accounting' ? ' · by accounting month' : '')
          : undefined}
      />
      <div className="grid cols-4">
        <Stat
          label="Money in"
          value={totals.income}
          tone="pos"
          note={`${compact(totals.average_monthly_income)} average per month`}
          onDrill={() => drill({
            title: 'Money in',
            subtitle: 'Everything counted as income in this period — pay, interest and '
              + 'anything else that genuinely came in. Refunds and repayments are '
              + 'counted against spending instead, so they are not here.',
            params: { flow_role: 'income' },
          })}
        />
        <Stat
          label="Money out"
          value={totals.spend}
          tone="neg"
          note={`${compact(totals.average_monthly_spend)} average per month`}
          onDrill={() => drill({
            title: 'Money out',
            subtitle: 'Spending, net of anything that came back against it. EMIs and '
              + 'SIPs are money leaving too, but they are commitments and investments '
              + 'rather than spending — they have their own tiles.',
            params: { flow_role: SPEND_ROLES },
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
          tone="accent"
          note={`${count(totals.transaction_count)} transactions analysed`}
          onDrill={() => drill({
            title: 'Invested',
            subtitle: 'Money moved into investments. Still yours, so it counts as saved '
              + 'rather than spent.',
            params: { flow_role: 'investment' },
          })}
        />
      </div>

      {/* ---- cashflow ---- */}
      <div className="split">
        <Card
          title="Income against outflow, by month"
          sub="Outflow includes EMIs and SIPs. Click a month for its rows."
        >
          {monthly.length ? (
            <>
              <ComboChart
                data={monthly}
                animate={prefs.animate}
                bars={[
                  { key: 'income', name: 'Income', color: 'var(--c2)' },
                  { key: 'total_outflow', name: 'Outflow', color: 'var(--c7)' },
                ]}
                line={{ key: 'net', name: 'Net', color: 'var(--c1)' }}
                onPick={(m) => drill({
                  title: m.label,
                  subtitle: `${money(m.income)} in, ${money(m.spend)} out, ${money(m.net)} `
                    + 'net — everything counted in this month.',
                  ignorePeriod: true,
                  periodLabel: m.label,
                  sortBy: 'date',
                  params: { start_month: m.month, end_month: m.month },
                })}
              />
              <Legend items={[
                { label: 'Income', color: 'var(--c2)' },
                { label: 'Outflow', color: 'var(--c7)' },
                { label: 'Net', color: 'var(--c1)' },
              ]} />
            </>
          ) : (
            <div className="dim small">No monthly figures in this period.</div>
          )}
        </Card>

        <Card
          title="Where the money went"
          sub={`${categories.length} categories — click one for the rows`}
        >
          <BarList
            items={categories}
            total={totals.spend}
            max={11}
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
              why - and the card simply looks wrong. */}
          {totals.offsets > 0 && (
            <p className="tiny dim" style={{ marginTop: 10, lineHeight: 1.6 }}>
              {money(totals.gross_spend)} went out and {money(totals.offsets)} came back
              as refunds and reimbursements, which is the {money(totals.spend)} counted
              above. The bars show what went out.
            </p>
          )}
        </Card>
      </div>

      {/* ---- findings ---- */}
      {narrative?.key_findings?.length > 0 && (
        <>
          <Section title="What stands out" note={scoped ? 'whole ledger' : undefined} />
          <Card pad>
            <div className="col" style={{ gap: 13 }}>
              {narrative.key_findings.map((f, i) => (
                <div key={i} className="row" style={{ alignItems: 'flex-start' }}>
                  <Chip tone={SEVERITY[f.severity] || 'acc'}>{f.severity || 'info'}</Chip>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{f.title}</div>
                    <div className="small muted">{f.detail}</div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}

      {narrative?.where_money_went && (
        <>
          <Section title="Following the salary" note={scoped ? 'whole ledger' : undefined} />
          <Card pad><div className="prose"><p>{narrative.where_money_went}</p></div></Card>
        </>
      )}

      {narrative?.observations?.length > 0 && (
        <>
          <Section title="Options worth knowing about"
            note={scoped ? 'whole ledger' : undefined} />
          <Card pad>
            <div className="col" style={{ gap: 13 }}>
              {narrative.observations.map((o, i) => (
                <div key={i} className="row" style={{ alignItems: 'flex-start' }}>
                  <span className="dot" style={{ background: colorFor(i), marginTop: 7 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{o.title}</div>
                    <div className="small muted">{o.detail}</div>
                    {o.mechanism && <div className="tiny dim" style={{ marginTop: 3 }}>{o.mechanism}</div>}
                  </div>
                </div>
              ))}
            </div>
            <Callout style={{ marginTop: 14 }}>
              These are mechanical observations from your own numbers, not financial
              advice. Anything involving investing, prepaying or restructuring debt is
              worth discussing with a qualified adviser who can see your full picture.
            </Callout>
          </Card>
        </>
      )}

      {/* ---- position ----
          As at the END of the window, read from the balance each statement
          printed after its last row up to that date - not "latest known",
          which is a figure that refuses to move however far back you look. */}
      <Section
        title="Position"
        note={position.basis === 'period'
          ? `as at ${dateLabel(position.as_of)}`
          : position.as_of
            ? `latest known balances, as at ${dateLabel(position.as_of)}`
              + (scoped ? ` — not ${periodLabel}` : '')
            : `latest known balances${scoped ? ` — not ${periodLabel}` : ''}`}
      />
      <div className="grid cols-3">
        <Stat label="Assets tracked" value={netWorth._assets}
          note="Cash balances, as printed on the statements" />
        <Stat label="Liabilities" value={netWorth._liabilities} tone="neg"
          note="Loans and card dues outstanding" />
        <Stat label="Net position" value={netWorth._net}
          tone={netWorth._net >= 0 ? 'pos' : 'neg'}
          note="Assets minus liabilities on tracked accounts" />
      </div>
      {position.missing?.length > 0 && position.basis === 'period' && (
        <Callout tone="warn">
          {/* Named rather than counted: a total quietly missing an account is
              worse than one that says which account it is missing. */}
          No balance could be established as at {dateLabel(position.as_of)} for{' '}
          {position.missing.join(', ')} — {position.missing.length === 1 ? 'it is' : 'they are'}{' '}
          left out of the figures above. Card statements often print no running balance.
        </Callout>
      )}

      {/* ---- data quality ----
          Two kinds of figure, labelled apart. "Uncategorised" is a property of
          the rows in this window and follows it. Files and transfer matching
          are properties of what has been imported, which is not a period at
          all - a file does not belong to March. */}
      <Section title="Data quality"
        note={scoped ? 'whole ledger, except where noted' : undefined} />
      <Card pad>
        <div className="grid cols-4" style={{ gap: 10, marginBottom: 12 }}>
          <QualityTile
            label="Files reconciled"
            value={`${quality?.files_reconciled ?? 0}/${quality?.files_processed ?? 0}`}
            tone={quality?.files_unreconciled ? 'warn' : 'pos'}
          />
          <QualityTile label="Rules-categorised" value={count(quality?.rules_settled ?? 0)} />
          <QualityTile
            label={scoped ? `Uncategorised in ${periodLabel}` : 'Uncategorised'}
            value={count(analysis.uncategorized?.count ?? 0)}
            tone={analysis.uncategorized?.count ? 'warn' : 'pos'}
            onDrill={analysis.uncategorized?.count ? () => drill({
              title: 'Uncategorised',
              subtitle: 'No rule matched these, so they sit outside the category '
                + 'breakdown.',
              params: { category: 'uncategorized' },
            }) : undefined}
          />
          <QualityTile label="Double-count avoided"
            value={compact(transfers?.double_count_avoided || 0)} tone="pos" />
        </div>

        <div className="col">
          {transfers?.notes?.map((n, i) => <Callout tone="pos" key={i}>{n}</Callout>)}
          {(data.narrative?.caveats || []).map((c, i) => (
            <Callout tone="warn" key={`c${i}`}>{c}</Callout>
          ))}
          {(analysis.notes || []).map((n, i) => <Callout key={`n${i}`}>{n}</Callout>)}
        </div>
      </Card>

      {/* Forecast and Owed both answer a question about the position this page
          describes - what happens next, and what is coming back. They are
          screens of their own now rather than the tail of this one, which was
          a page nobody scrolled to the end of. */}
      <div className="grid cols-2">
        <NextCard to="/forecast" icon="gauge" title="What happens next"
          body="Projected balance month by month, as a range rather than a number, with
                the commitments behind it." />
        <NextCard to="/owed" icon="hand" title="Money owed to you"
          body="Expenses that were never really yours, open until they come back — by
                transfer, on your card, or in cash." />
      </div>
    </>
  );
}

function QualityTile({ label, value, tone, onDrill }) {
  return (
    <div className="card sunken" style={{ padding: '10px 12px' }}>
      <div className="tiny dim" style={{ marginBottom: 3 }}>{label}</div>
      <div className={`num ${tone === 'pos' ? 'pos' : tone === 'warn' ? 'warnc' : ''}`}
        style={{ fontSize: 18, fontWeight: 640 }}>
        {onDrill
          ? <button type="button" className="drill" onClick={onDrill}
            title="Show these transactions">{value}</button>
          : value}
      </div>
    </div>
  );
}

function NextCard({ to, icon, title, body }) {
  return (
    <Link to={to} className="card" style={{ textDecoration: 'none', color: 'inherit' }}>
      <div className="card-body row" style={{ alignItems: 'flex-start' }}>
        <span className="empty-mark" style={{ width: 34, height: 34, marginBottom: 0 }}>
          <Icon name={icon} size={16} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 620 }}>{title}</div>
          <div className="small muted">{body}</div>
        </div>
        <Icon name="chevron" size={16} />
      </div>
    </Link>
  );
}
