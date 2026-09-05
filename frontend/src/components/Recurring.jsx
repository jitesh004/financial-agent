import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Callout, Card, Chip, ConfirmButton, Empty, Stat } from './ui';
import { api, dateLabel, money, titleCase } from '../lib';
import { usePeriod } from '../period';

/* Recurring commitments, each expandable to the transactions behind it.
 *
 * The expansion is the point: a series is an inference, and the only way to
 * judge whether it is right is to see the rows it was inferred from. */

/* Only a fallback now. The server stores `cadence_name` alongside the series,
   so this map is what answers for a row written before it did - and, being a
   second copy of the server's table, it cannot name a cadence the detector
   learns about later. That is the reason the column exists. */
const CADENCE = {
  7: 'weekly', 14: 'fortnightly', 28: 'four-weekly', 30: 'monthly',
  61: 'bi-monthly', 91: 'quarterly', 182: 'half-yearly', 365: 'yearly',
};

function cadenceLabel(series) {
  return series.cadence_name || CADENCE[series.cadence_days]
    || `every ${series.cadence_days} days`;
}

/* How a series' amount has moved, said in the tense that matters: a price
   that ROSE is a fact about the past, and what the user needs from it is
   what the next charge will be. */
const TREND = {
  rose: ['warn', 'price went up'],
  fell: ['pos', 'price came down'],
  drifting: ['accent', 'drifting'],
};

function Evidence({ series }) {
  if (!series.evidence?.length) return null;
  return (
    <ul style={{
      margin: '0 0 12px', paddingLeft: 18, color: 'var(--text-2)',
      fontSize: 13, lineHeight: 1.6,
    }}
    >
      {series.evidence.map((line, i) => <li key={i}>{line}</li>)}
    </ul>
  );
}

export default function Recurring() {
  const [series, setSeries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [members, setMembers] = useState({});
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState('');

  const { label: periodLabel, scoped, window: resolved } = usePeriod();

  const load = useCallback(() => {
    setLoading(true);
    api.recurring()
      .then((rows) => { setSeries(rows || []); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  /* A series is a fact about a stretch of time, not about one row, so the
     period narrows this list by OVERLAP: a commitment that was running at any
     point in the window belongs in it. Filtering by "last seen inside the
     window" would hide a live standing instruction whose last payment landed
     the day before the window opened.

     Done here rather than server-side because a series carries its own span -
     first_seen to last_seen - and comparing two spans needs no query. The
     comparison is against the window's NOMINAL calendar bounds, which for a
     month window are its month boundaries; a series spans months by
     definition, so the day or two an accounting month reaches past them
     cannot change whether one overlaps. */
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

  async function expand(s) {
    if (open === s.id) { setOpen(null); return; }
    setOpen(s.id);
    if (members[s.id]) return;
    try {
      // The series id is stamped onto each member row, so the rows behind a
      // series come straight from the ledger rather than being re-derived.
      const res = await api.transactions({ limit: 500, sort_by: 'date', sort_dir: 'desc' });
      setMembers((prev) => ({
        ...prev,
        [s.id]: (res.transactions || []).filter((t) => t.recurring_series_id === s.id),
      }));
    } catch (e) {
      setError(e.message);
    }
  }

  async function patch(id, fields) {
    try {
      await api.updateSeries(id, fields);
      setEditing(null);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function remove(id) {
    try {
      await api.deleteSeries(id);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  if (loading) return <div className="spinner" style={{ margin: 40 }} />;

  const active = visible.filter((s) => s.is_active);
  /* Both server shapes carry `amount` and `monthly_equivalent` now - see
     serializers.stored_recurring_json - so this no longer has to normalise
     one into the other, and there is no longer a second copy of the
     cadence-to-monthly conversion living here. The old fallback divided 30.44
     days by a nominal 30-day cadence, which published every monthly
     commitment 1.5% above what the statement said and compounded across the
     fourteen of them this stat adds up. */
  const seriesAmount = (s) => Number(s.amount ?? s.median_amount) || 0;
  const monthlyTotal = active.reduce((sum, s) => {
    if (s.direction !== 'debit') return sum;
    return sum + (Number(s.monthly_equivalent) || 0);
  }, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>
          Recurring
          {scoped && <span className="section-note">{periodLabel}</span>}
        </h2>
        <p style={{ color: 'var(--text-2)', margin: 0 }}>
          Click any row to see why it was called a series, and the
          transactions it was inferred from.
          {scoped && ' Showing the commitments that were running at any point '
            + 'in this period.'}
        </p>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}

      <div className="grid cols-3">
        {/* Counts as strings: Stat renders any number as currency, and
            `precise` only changes the decimal places. */}
        <Stat label="Tracked series" value={String(active.length)} />
        <Stat label="Committed per month" value={monthlyTotal} tone="neg"
              note="normalised to a monthly figure" />
        <Stat label="Not tracked" value={String(visible.length - active.length)} />
      </div>

      {!visible.length && (
        <Empty title={series.length
          ? `No recurring series running in ${periodLabel}`
          : 'No recurring series detected'}>
          {series.length
            ? `${series.length} series were detected in your ledger, none of `
              + 'them in this period. Widen the period to see them.'
            : 'A series needs at least three occurrences at a steady interval '
              + 'and a reasonably stable amount. If your ledger has not been '
              + 're-analysed since periods were introduced, re-parse from the '
              + 'Data tab first.'}
        </Empty>
      )}

      {visible.map((s) => {
        const isOpen = open === s.id;
        const rows = members[s.id];
        return (
          <Card key={s.id} style={{ opacity: s.is_active ? 1 : 0.55 }}>
            <div
              role="button"
              tabIndex={0}
              onClick={() => expand(s)}
              onKeyDown={(e) => (e.key === 'Enter' ? expand(s) : null)}
              style={{
                display: 'flex', justifyContent: 'space-between',
                alignItems: 'center', gap: 12, cursor: 'pointer',
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                {editing === s.id ? (
                  <div style={{ display: 'flex', gap: 8 }} onClick={(e) => e.stopPropagation()}>
                    <input
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      style={{ flex: 1 }}
                    />
                    <button className="btn primary"
                            onClick={() => patch(s.id, { label: draft })}>
                      Save
                    </button>
                    <button className="btn" onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div style={{ fontWeight: 600, fontSize: 15 }}>
                    <span style={{ color: 'var(--text-3)', marginRight: 8 }}>
                      {isOpen ? '▾' : '▸'}
                    </span>
                    {s.label}
                  </div>
                )}
                <div style={{
                  color: 'var(--text-2)', fontSize: 13, marginTop: 4,
                  display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center',
                }}
                >
                  <Chip>{titleCase(s.category)}</Chip>
                  <Chip>{cadenceLabel(s)}</Chip>
                  <span>{s.occurrences} occurrences</span>
                  {s.missed > 0 && (
                    <span title="Periods in the span with no charge at all">
                      · {s.missed} missed
                    </span>
                  )}
                  {s.next_expected && <span>· next {dateLabel(s.next_expected)}</span>}
                  {TREND[s.amount_trend] && (
                    <Chip tone={TREND[s.amount_trend][0]}>
                      {TREND[s.amount_trend][1]}
                    </Chip>
                  )}
                  {s.status === 'overdue' && <Chip tone="warn">overdue</Chip>}
                  {s.status === 'ended' && <Chip>ended</Chip>}
                  {!s.is_active && <Chip tone="warn">not tracked</Chip>}
                </div>
              </div>

              <div style={{ textAlign: 'right' }} onClick={(e) => e.stopPropagation()}>
                <div
                  className="num"
                  style={{
                    fontWeight: 600, fontSize: 16,
                    color: s.direction === 'credit' ? 'var(--positive)' : 'var(--text)',
                  }}
                >
                  {money(seriesAmount(s))}
                </div>
                {/* A series whose price changed has two amounts, and only one
                    of them is next month's bill. Showing the old one beside
                    the new is what makes the number above checkable against
                    a statement from before the change. */}
                {s.lifetime_median != null
                  && Math.abs(s.lifetime_median - seriesAmount(s)) > 1 && (
                  <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                    was {money(s.lifetime_median)}
                    {s.changed_on ? ` until ${dateLabel(s.changed_on)}` : ''}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                  <button
                    className="btn"
                    onClick={() => { setEditing(s.id); setDraft(s.label); }}
                  >
                    Rename
                  </button>
                  <button
                    className="btn"
                    onClick={() => patch(s.id, { is_active: !s.is_active })}
                  >
                    {s.is_active ? 'Ignore' : 'Track'}
                  </button>
                  <ConfirmButton className="btn danger"
                    question="Stop tracking this series? The transactions stay."
                    confirmLabel="Stop tracking"
                    onConfirm={() => remove(s.id)}>
                    Delete
                  </ConfirmButton>
                </div>
              </div>
            </div>

            {isOpen && (
              <div style={{
                marginTop: 14, paddingTop: 14,
                borderTop: '1px solid var(--surface-2)',
              }}
              >
                <Evidence series={s} />
                {!rows && <div className="spinner" />}
                {rows && !rows.length && (
                  <div style={{ color: 'var(--text-3)' }}>
                    No transactions are currently linked to this series. Series
                    ids are re-stamped when the ledger is analysed — re-parse
                    if this looks wrong.
                  </div>
                )}
                {rows && rows.length > 0 && (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th>Description</th>
                          <th>Category</th>
                          <th className="right">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((t) => (
                          <tr key={t.id}>
                            <td className="nowrap">{dateLabel(t.date)}</td>
                            <td>
                              <div style={{ wordBreak: 'break-word', whiteSpace: 'normal', lineHeight: '1.4' }}>
                                {t.description}
                              </div>
                            </td>
                            <td className="nowrap">{titleCase(t.category)}</td>
                            <td className="right num nowrap">
                              {money(Math.abs(t.amount))}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}
