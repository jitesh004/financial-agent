/* The control that says which period every screen is showing.
 *
 * Three things it has to do, and the third is the one usually missed:
 *
 *   1. Offer the periods people actually ask for, in one click.
 *   2. Let a window be drawn by hand - in months, or in exact dates.
 *   3. Say what the window it resolved to IS. "Last 3 months" is not a fact;
 *      "Jan 2026 – Mar 2026, by accounting month" is. A filter that does not
 *      show its own bounds is a filter you have to trust.
 *
 * The presets pick whole ACCOUNTING months - the month the ledger counts each
 * row in. That is why "August" here includes the salary that arrived on 1
 * September when that is August's pay: the alternative bucketing puts two
 * salaries in one month and leaves the next one empty.
 */

import React, { useEffect, useState } from 'react';
import { isCustom, usePeriod } from '../core/period';
import { monthLabelLong } from '../core/format';
import { Button, Icon, Segmented, Select } from '../ui';

function MonthSelect({ value, months, placeholder, onChange, label }) {
  return (
    <select value={value || ''} aria-label={label}
      onChange={(e) => onChange(e.target.value || null)} style={{ minWidth: 150 }}>
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

export default function PeriodBar() {
  const {
    period, setPeriod, presets, quickPresets, months, label,
    window: resolved, scoped, latest, earliest,
  } = usePeriod();
  const [open, setOpen] = useState(() => isCustom(period));

  // A period restored from a previous session can arrive already custom.
  useEffect(() => { if (isCustom(period)) setOpen(true); }, [period.preset]);

  const pick = (value) => {
    if (value === 'custom_months') {
      // Seeded with the latest month there is data for, so the drawer opens on
      // something real rather than on two empty pickers.
      setPeriod({ preset: 'custom_months', start_month: latest || null, end_month: latest || null });
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

  const others = presets.filter((p) => !quickPresets.some((q) => q.value === p.value));

  return (
    <div className="period">
      <span className="period-legend">Period</span>

      <Segmented
        ariaLabel="Period"
        value={period.preset}
        onChange={pick}
        options={quickPresets.map((p) => [
          p.value, p.short || p.label,
          p.resolved_label ? `${p.label} — ${p.resolved_label}` : p.label,
        ])}
      />

      {/* Everything else, including the two custom shapes. A select rather
          than more buttons: twelve of these on one row is a strip nobody
          reads. */}
      <Select
        aria-label="Other periods"
        value={others.some((o) => o.value === period.preset) ? period.preset : ''}
        onChange={(v) => v && pick(v)}
        placeholder={others.some((o) => o.value === period.preset)
          ? 'Other periods…' : 'Or a longer period…'}
        options={others.map((o) => [
          o.value, o.resolved_label ? `${o.label} — ${o.resolved_label}` : o.label,
        ])}
        style={{ maxWidth: 220 }}
      />

      {scoped && (
        <Button size="sm" onClick={() => pick('all')} title="Show the whole ledger again">
          Clear
        </Button>
      )}

      <div className="period-resolved">
        {/* The bounds, always. This is the line that makes the control honest
            rather than merely convenient. */}
        <strong>{label}</strong>
        {resolved && (
          <span className="tiny dim">
            {resolved.basis === 'accounting'
              ? `${resolved.months ? `${resolved.months} month${resolved.months === 1 ? '' : 's'} · ` : ''}by accounting month`
              : 'by transaction date'}
          </span>
        )}
      </div>

      {open && isCustom(period) && (
        <div className="period-custom">
          <Segmented
            ariaLabel="Custom period type"
            value={period.preset}
            onChange={pick}
            options={[['custom_months', 'Months'], ['custom', 'Exact dates']]}
          />
          {period.preset === 'custom_months' ? (
            <>
              <MonthSelect label="First month" value={period.start_month} months={months}
                placeholder="From the beginning"
                onChange={(v) => setPeriod({ ...period, start_month: v })} />
              <Icon name="arrow-right" size={13} />
              <MonthSelect label="Last month" value={period.end_month} months={months}
                placeholder="Up to the latest"
                onChange={(v) => setPeriod({ ...period, end_month: v })} />
              <span className="tiny dim">
                Whole accounting months — a salary paid on the 1st still counts in the
                month it is pay for.
              </span>
            </>
          ) : (
            <>
              <input type="date" aria-label="First date" value={period.start || ''}
                min={earliest ? `${earliest}-01` : undefined}
                onChange={(e) => setPeriod({ ...period, start: e.target.value })} />
              <Icon name="arrow-right" size={13} />
              <input type="date" aria-label="Last date" value={period.end || ''}
                onChange={(e) => setPeriod({ ...period, end: e.target.value })} />
              <span className="tiny dim">
                Exact transaction dates — the literal reading, so a salary is counted on
                the day it arrived.
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
 * Shown instead of a dashboard of zeros: a period with no rows in it looks
 * identical to a month where nothing happened, and the difference matters -
 * one is a fact about your spending, the other about your window. */
export function PeriodEmpty({ available }) {
  const { label, setPeriod } = usePeriod();
  const latest = available?.latest;
  return (
    <div className="empty">
      <span className="empty-mark"><Icon name="calendar" size={19} /></span>
      <h3>Nothing counted in {label}</h3>
      <p>
        {latest
          ? 'Either no statement covering it has been parsed, or the period is outside '
            + 'what has been imported.'
          : 'No statements have been analysed yet.'}
      </p>
      {latest && (
        <div className="row" style={{ justifyContent: 'center', marginTop: 6 }}>
          <Button variant="primary" onClick={() => setPeriod({
            preset: 'custom_months', start_month: latest, end_month: latest,
          })}>
            Go to {monthLabelLong(latest)}
          </Button>
          <Button onClick={() => setPeriod({ preset: 'all' })}>Show all time</Button>
        </div>
      )}
    </div>
  );
}
