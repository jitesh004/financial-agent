import React, { useCallback, useEffect, useState } from 'react';
import { Callout, Card, Chip, Empty, Stat } from './ui';
import { api, dateLabel, money, titleCase } from '../lib';

/* Recurring commitments, each expandable to the transactions behind it.
 *
 * The expansion is the point: a series is an inference, and the only way to
 * judge whether it is right is to see the rows it was inferred from. */

const CADENCE = {
  7: 'weekly', 14: 'fortnightly', 30: 'monthly', 61: 'bi-monthly',
  91: 'quarterly', 182: 'half-yearly', 365: 'yearly',
};

function cadenceLabel(series) {
  return series.cadence_name || CADENCE[series.cadence_days]
    || `every ${series.cadence_days} days`;
}

export default function Recurring() {
  const [series, setSeries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [members, setMembers] = useState({});
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    api.recurring()
      .then((rows) => { setSeries(rows || []); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

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
    if (!window.confirm('Stop tracking this series? The transactions stay.')) return;
    try {
      await api.deleteSeries(id);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  if (loading) return <div className="spinner" style={{ margin: 40 }} />;

  const active = series.filter((s) => s.is_active);
  const monthlyTotal = active.reduce((sum, s) => {
    const amount = Number(s.median_amount) || 0;
    const days = Number(s.cadence_days) || 30;
    return sum + (s.direction === 'debit' ? (amount * 30.44) / days : 0);
  }, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>Recurring</h2>
        <p style={{ color: 'var(--text-2)', margin: 0 }}>
          Click any row to see the transactions it was inferred from.
        </p>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}

      <div className="grid cols-3">
        {/* Counts as strings: Stat renders any number as currency, and
            `precise` only changes the decimal places. */}
        <Stat label="Tracked series" value={String(active.length)} />
        <Stat label="Committed per month" value={monthlyTotal} tone="neg"
              note="normalised to a monthly figure" />
        <Stat label="Not tracked" value={String(series.length - active.length)} />
      </div>

      {!series.length && (
        <Empty title="No recurring series detected">
          A series needs at least three occurrences at a steady interval and a
          reasonably stable amount. If your ledger has not been re-analysed
          since periods were introduced, re-parse from the Data tab first.
        </Empty>
      )}

      {series.map((s) => {
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
                  {s.next_expected && <span>· next {dateLabel(s.next_expected)}</span>}
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
                  {money(Number(s.median_amount) || 0)}
                </div>
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
                  <button className="btn danger" onClick={() => remove(s.id)}>
                    Delete
                  </button>
                </div>
              </div>
            </div>

            {isOpen && (
              <div style={{
                marginTop: 14, paddingTop: 14,
                borderTop: '1px solid var(--surface-2)',
              }}
              >
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
                              <div className="truncate" title={t.description}>
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
