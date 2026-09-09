/* Recurring commitments, each expandable to the transactions behind it.
 *
 * The expansion is the point: a series is an inference, and the only way to
 * judge whether it is right is to see the rows it was inferred from.
 */

import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { usePeriod } from '../core/period';
import { useToast } from '../core/toast';
import { dateLabel, money, titleCase } from '../core/format';
import {
  Button, Callout, Card, Chip, ConfirmButton, Empty, IconButton, Loading, Stat,
  Table,
} from '../ui';

/* Only a fallback. The server stores `cadence_name` alongside the series, so
   this map answers for a row written before it did - and, being a second copy
   of the server's table, it cannot name a cadence the detector learns about
   later. That is the reason the column exists at all. */
const CADENCE = {
  7: 'weekly', 14: 'fortnightly', 28: 'four-weekly', 30: 'monthly',
  61: 'bi-monthly', 91: 'quarterly', 182: 'half-yearly', 365: 'yearly',
};

/* How a series' amount has moved, said in the tense that matters: a price that
   ROSE is a fact about the past, and what somebody needs from it is what the
   next charge will be. */
const TREND = {
  rose: ['warn', 'price went up'],
  fell: ['pos', 'price came down'],
  drifting: ['acc', 'drifting'],
};

const cadenceLabel = (s) => s.cadence_name || CADENCE[s.cadence_days]
  || `every ${s.cadence_days} days`;

export default function Recurring() {
  const toast = useToast();
  const { label: periodLabel, scoped, window: resolved } = usePeriod();
  const { data: series = [], loading, error, refetch } =
    useQuery('recurring', () => api.recurring());
  const [open, setOpen] = useState(null);
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState('');

  /* The rows behind the open series, asked for BY series.
   *
   * This used to pull the ledger's most recent two thousand rows - capped to
   * one thousand by the server - and sieve them in the browser for a matching
   * series id. Anything whose payments fall outside that page found nothing
   * and reported "no transactions are currently linked to this series", which
   * on a ledger of any size is most of them. The key is per series too: one
   * shared key meant opening a second series showed the first one's rows until
   * the request came back. */
  const { data: memberRows, loading: loadingMembers } = useQuery(
    open ? `recurring-members:${open}` : null,
    () => api.transactions({
      recurring_series_id: open, limit: 500, sort_by: 'date', sort_dir: 'desc',
    }),
    { enabled: Boolean(open) },
  );

  /* A series is a fact about a stretch of time, not about one row, so the
     period narrows this list by OVERLAP: a commitment running at any point in
     the window belongs in it. Filtering by "last seen inside the window" would
     hide a live standing instruction whose last payment landed the day before
     the window opened. */
  const visible = useMemo(() => {
    if (!scoped || !resolved) return series;
    const from = resolved.start;
    const until = resolved.end;
    return series.filter((s) => {
      const first = s.first_seen || s.last_seen;
      const last = s.last_seen || s.first_seen;
      if (!first || !last) return true;
      if (until && first > until) return false;
      return !(from && last < from);
    });
  }, [series, scoped, resolved]);

  async function patch(id, fields) {
    try {
      await api.updateSeries(id, fields);
      setEditing(null);
      invalidate('recurring');
      refetch();
    } catch (e) { toast.fail('That change was not saved', e.message); }
  }

  async function remove(id) {
    try {
      await api.deleteSeries(id);
      invalidate('recurring');
      refetch();
      toast.ok('Stopped tracking that series', 'The transactions stay where they are.');
    } catch (e) { toast.fail('It could not be removed', e.message); }
  }

  if (loading) return <Loading label="Finding what repeats…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  const active = visible.filter((s) => s.is_active);
  /* Both server shapes carry `amount` and `monthly_equivalent`, so there is no
     second copy of the cadence-to-monthly conversion here. The old fallback
     divided 30.44 days by a nominal 30-day cadence, publishing every monthly
     commitment 1.5% above what the statement said. */
  const amountOf = (s) => Number(s.amount ?? s.median_amount) || 0;
  const monthlyTotal = active.reduce(
    (sum, s) => (s.direction === 'debit' ? sum + (Number(s.monthly_equivalent) || 0) : sum), 0);

  return (
    <>
      <div>
        <h2 className="h2">
          Recurring
          {scoped && <span className="section-note" style={{ marginLeft: 10 }}>{periodLabel}</span>}
        </h2>
        <p className="lead">
          Click any row to see why it was called a series, and the transactions it was
          inferred from.
          {scoped && ' Showing the commitments that were running at any point in this period.'}
        </p>
      </div>

      <div className="grid cols-3">
        {/* Counts as strings: Stat renders any number as currency. */}
        <Stat label="Tracked series" value={String(active.length)} />
        <Stat label="Committed per month" value={monthlyTotal} tone="neg"
          note="normalised to a monthly figure" />
        <Stat label="Not tracked" value={String(visible.length - active.length)} />
      </div>

      {!visible.length && (
        <Empty icon="repeat" title={series.length
          ? `No recurring series running in ${periodLabel}`
          : 'No recurring series detected'}>
          {series.length
            ? `${series.length} series were detected in your ledger, none of them in this `
              + 'period. Widen the period to see them.'
            : 'A series needs at least three occurrences at a steady interval and a '
              + 'reasonably stable amount. If your ledger has not been re-analysed since '
              + 'periods were introduced, rebuild it from the Data screen first.'}
        </Empty>
      )}

      {visible.map((s) => {
        const isOpen = open === s.id;
        const rows = isOpen && !loadingMembers ? (memberRows?.transactions || []) : null;
        /* pad={false}: the body below is this card's own `.card-body`. Letting
           Card add one as well nests two and pads every series twice. */
        return (
          <Card key={s.id} className={s.is_active ? '' : 'sunken'} pad={false}
            style={{ opacity: s.is_active ? 1 : 0.62 }}>
            <div className="card-body">
              <div className="row" style={{ alignItems: 'flex-start' }}>
                <IconButton
                  icon={isOpen ? 'chevron-down' : 'chevron'}
                  size="sm" className="ghost"
                  label={isOpen ? 'Hide the rows' : 'Show the rows'}
                  onClick={() => setOpen(isOpen ? null : s.id)}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  {editing === s.id ? (
                    <div className="row">
                      <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
                        style={{ flex: 1 }} />
                      <Button variant="primary" size="sm"
                        onClick={() => patch(s.id, { label: draft })}>Save</Button>
                      <Button size="sm" onClick={() => setEditing(null)}>Cancel</Button>
                    </div>
                  ) : (
                    <button type="button" className="drill"
                      style={{ fontWeight: 620, fontSize: 15 }}
                      onClick={() => setOpen(isOpen ? null : s.id)}>
                      {s.label}
                    </button>
                  )}
                  <div className="row tight" style={{ marginTop: 5 }}>
                    <Chip>{titleCase(s.category)}</Chip>
                    <Chip>{cadenceLabel(s)}</Chip>
                    <span className="small muted">{s.occurrences} occurrences</span>
                    {s.missed > 0 && (
                      <span className="small muted" title="Periods in the span with no charge">
                        · {s.missed} missed
                      </span>
                    )}
                    {s.next_expected && (
                      <span className="small muted">· next {dateLabel(s.next_expected)}</span>
                    )}
                    {TREND[s.amount_trend] && (
                      <Chip tone={TREND[s.amount_trend][0]}>{TREND[s.amount_trend][1]}</Chip>
                    )}
                    {s.status === 'overdue' && <Chip tone="warn">overdue</Chip>}
                    {s.status === 'ended' && <Chip>ended</Chip>}
                    {!s.is_active && <Chip tone="warn">not tracked</Chip>}
                  </div>
                </div>

                <div className="right">
                  <div className="num" style={{
                    fontWeight: 640, fontSize: 16,
                    color: s.direction === 'credit' ? 'var(--pos)' : 'var(--text)',
                  }}>
                    {money(amountOf(s))}
                  </div>
                  {/* A series whose price changed has two amounts, and only one
                      is next month's bill. Showing the old one beside the new
                      is what makes the number above checkable against a
                      statement from before the change. */}
                  {s.lifetime_median != null
                    && Math.abs(s.lifetime_median - amountOf(s)) > 1 && (
                    <div className="tiny dim">
                      was {money(s.lifetime_median)}
                      {s.changed_on ? ` until ${dateLabel(s.changed_on)}` : ''}
                    </div>
                  )}
                  <div className="row tight" style={{ marginTop: 8, justifyContent: 'flex-end' }}>
                    <Button size="sm" icon="edit"
                      onClick={() => { setEditing(s.id); setDraft(s.label); }}>
                      Rename
                    </Button>
                    <Button size="sm" onClick={() => patch(s.id, { is_active: !s.is_active })}>
                      {s.is_active ? 'Ignore' : 'Track'}
                    </Button>
                    <ConfirmButton
                      size="sm" variant="danger"
                      question="Stop tracking this series? The transactions stay."
                      confirmLabel="Stop tracking"
                      onConfirm={() => remove(s.id)}
                    >
                      Delete
                    </ConfirmButton>
                  </div>
                </div>
              </div>

              {isOpen && (
                <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--line)' }}>
                  {s.evidence?.length > 0 && (
                    <ul className="small muted" style={{ paddingLeft: 18, marginBottom: 12 }}>
                      {s.evidence.map((line, i) => (
                        <li key={i} style={{ listStyle: 'disc', lineHeight: 1.7 }}>{line}</li>
                      ))}
                    </ul>
                  )}
                  {!rows && <Loading label="Reading the rows behind it…" pad={12} />}
                  {rows && !rows.length && (
                    <div className="small dim">
                      No transactions are currently linked to this series. Series ids are
                      re-stamped when the ledger is rebuilt — rebuild if this looks wrong.
                    </div>
                  )}
                  {rows && rows.length > 0 && (
                    <Table scrollY>
                      <thead>
                        <tr>
                          <th>Date</th><th>Description</th><th>Category</th>
                          <th className="right">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((t) => (
                          <tr key={t.id}>
                            <td className="nowrap">{dateLabel(t.date)}</td>
                            <td><div style={{ overflowWrap: 'anywhere' }}>{t.description}</div></td>
                            <td className="nowrap">{titleCase(t.category)}</td>
                            <td className="right num nowrap">{money(Math.abs(t.amount))}</td>
                          </tr>
                        ))}
                      </tbody>
                    </Table>
                  )}
                </div>
              )}
            </div>
          </Card>
        );
      })}
    </>
  );
}
