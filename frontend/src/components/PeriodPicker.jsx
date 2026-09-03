import React, { useEffect, useState } from 'react';
import { monthLabelLong } from '../lib';
import { isCustom, usePeriod } from '../period';

/* The control that says which period every screen is showing.
 *
 * Three things it has to do, and the third is the one usually missed:
 *
 * 1. Offer the periods people actually ask for, in one click.
 * 2. Let a window be drawn by hand - in months, or in exact dates.
 * 3. Say what the window it resolved to IS. "Last 3 months" is not a fact;
 *    "Jan 2026 – Mar 2026, by accounting month" is. A filter that does not
 *    show its own bounds is a filter you have to trust.
 *
 * The preset buttons pick whole ACCOUNTING months - the month the ledger
 * counts each row in, the same rule the Months tab has always used. That is
 * why "August" here includes the salary that arrived on 1 September when that
 * is August's pay: the alternative bucketing puts two salaries in one month
 * and leaves the next one empty.
 */

/* Every month the ledger has rows in, newest first. Offering months with
   nothing in them would invite drawing a window that cannot contain anything. */
function MonthSelect({ value, months, placeholder, onChange, ariaLabel }) {
  return (
    <select className="period-input" value={value || ''} aria-label={ariaLabel}
      onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{placeholder}</option>
      {months.map((m) => (
        <option key={m.month} value={m.month}>
          {monthLabelLong(m.month)}
          {/* Spelled out: "May 2026 · 8" reads as part of a date. */}
          {m.count ? ` — ${m.count} row${m.count === 1 ? '' : 's'}` : ''}
        </option>
      ))}
    </select>
  );
}

export default function PeriodPicker({ compact = false }) {
  const {
    period, setPeriod, presets, quickPresets, months, label,
    window: resolved, scoped, latest, earliest,
  } = usePeriod();
  const [open, setOpen] = useState(() => isCustom(period));

  // The drawer is the picker's own state, but a period restored from a
  // previous session can arrive already custom.
  useEffect(() => { if (isCustom(period)) setOpen(true); }, [period.preset]);

  const pick = (value) => {
    if (value === 'custom_months') {
      // Seeded with the latest month there is data for, so the drawer opens
      // on something real rather than on two empty pickers.
      setPeriod({ preset: 'custom_months', start_month: latest || null,
                  end_month: latest || null });
      setOpen(true);
      return;
    }
    if (value === 'custom') {
      setPeriod({ preset: 'custom', start: '', end: '' });
      setOpen(true);
      return;
    }
    setPeriod({ preset: value });
    setOpen(false);
  };

  const others = presets.filter(
    (p) => !quickPresets.some((q) => q.value === p.value));

  return (
    <div className={`period ${compact ? 'compact' : ''}`}>
      <div className="period-row">
        <span className="period-legend">Period</span>

        <div className="seg period-seg" role="group" aria-label="Period">
          {quickPresets.map((one) => (
            <button
              key={one.value}
              className={`seg-btn ${period.preset === one.value ? 'active' : ''}`}
              title={one.resolved_label
                ? `${one.label} — ${one.resolved_label}` : one.label}
              onClick={() => pick(one.value)}
            >
              {one.short || one.label}
            </button>
          ))}
        </div>

        {/* Everything else, including the two custom shapes. A select rather
            than more buttons: twelve of these on one row is a strip nobody
            reads, which is the failure the grouped nav already fixed once. */}
        <select
          className="period-input"
          aria-label="More periods"
          value={others.some((o) => o.value === period.preset) ? period.preset : ''}
          onChange={(e) => e.target.value && pick(e.target.value)}
        >
          <option value="">More…</option>
          {others.map((one) => (
            <option key={one.value} value={one.value}>
              {one.label}
              {one.resolved_label ? ` — ${one.resolved_label}` : ''}
            </option>
          ))}
        </select>

        {scoped && (
          <button className="btn period-clear" onClick={() => pick('all')}
            title="Show the whole ledger again">
            Clear
          </button>
        )}

        <div className="period-resolved">
          {/* The bounds, always. This is the line that makes the control
              honest rather than merely convenient. */}
          <strong>{label}</strong>
          {resolved && (
            <span className="period-basis">
              {resolved.basis === 'accounting'
                ? `${resolved.months ? `${resolved.months} month${resolved.months === 1 ? '' : 's'} · ` : ''}by accounting month`
                : 'by transaction date'}
            </span>
          )}
        </div>
      </div>

      {open && isCustom(period) && (
        <div className="period-custom">
          <div className="seg" role="group" aria-label="Custom period type">
            <button
              className={`seg-btn ${period.preset === 'custom_months' ? 'active' : ''}`}
              onClick={() => pick('custom_months')}
            >
              Months
            </button>
            <button
              className={`seg-btn ${period.preset === 'custom' ? 'active' : ''}`}
              onClick={() => pick('custom')}
            >
              Exact dates
            </button>
          </div>

          {period.preset === 'custom_months' ? (
            <>
              <MonthSelect
                ariaLabel="First month"
                value={period.start_month}
                months={months}
                placeholder="From the beginning"
                onChange={(value) => setPeriod({ ...period, start_month: value })}
              />
              <span className="period-dash">→</span>
              <MonthSelect
                ariaLabel="Last month"
                value={period.end_month}
                months={months}
                placeholder="Up to the latest"
                onChange={(value) => setPeriod({ ...period, end_month: value })}
              />
              <span className="period-note">
                Whole accounting months — a salary paid on the 1st still counts
                in the month it is pay for.
              </span>
            </>
          ) : (
            <>
              <input
                className="period-input" type="date" aria-label="First date"
                value={period.start || ''}
                min={earliest ? `${earliest}-01` : undefined}
                onChange={(e) => setPeriod({ ...period, start: e.target.value })}
              />
              <span className="period-dash">→</span>
              <input
                className="period-input" type="date" aria-label="Last date"
                value={period.end || ''}
                onChange={(e) => setPeriod({ ...period, end: e.target.value })}
              />
              <span className="period-note">
                Exact transaction dates — the literal reading, so a salary is
                counted on the day it arrived.
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/* "There is nothing in this window", with somewhere to go about it.
 *
 * Shown instead of an empty screen: a period with no rows in it looks
 * identical to a month where nothing happened, and the difference matters -
 * one is a fact about your spending, the other about your window. */
export function PeriodEmpty({ available }) {
  const { label, setPeriod } = usePeriod();
  const latest = available?.latest;
  return (
    <div className="empty">
      <h3>Nothing counted in {label}</h3>
      <p>
        {latest
          ? 'Either no statement covering it has been parsed, or the period is '
            + 'outside what has been imported.'
          : 'No statements have been analyzed yet.'}
      </p>
      {latest && (
        <div style={{ marginTop: 14, display: 'flex', gap: 8,
          justifyContent: 'center', flexWrap: 'wrap' }}>
          <button className="btn primary"
            onClick={() => setPeriod({ preset: 'custom_months',
              start_month: latest, end_month: latest })}>
            Go to {monthLabelLong(latest)}
          </button>
          <button className="btn" onClick={() => setPeriod({ preset: 'all' })}>
            Show all time
          </button>
        </div>
      )}
    </div>
  );
}
