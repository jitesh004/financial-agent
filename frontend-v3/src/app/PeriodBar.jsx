/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Period Bar
   Quick presets, custom accounting/calendar month selector & bounds label.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useEffect, useState } from 'react';
import { isCustom, usePeriod } from '../core/period';
import { monthLabelLong } from '../core/format';
import { Button, Icon, Segmented, Select } from '../ui';

function MonthSelect({ value, months, placeholder, onChange, label }) {
  return (
    <select
      value={value || ''}
      aria-label={label}
      onChange={(e) => onChange(e.target.value || null)}
      style={{
        padding: '5px 10px', fontSize: 13, borderRadius: 'var(--r-sm)',
        background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--line-strong)',
        minWidth: 150,
      }}
    >
      <option value="">{placeholder}</option>
      {months.map((m) => (
        <option key={m.month} value={m.month}>
          {monthLabelLong(m.month)}
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

  useEffect(() => { if (isCustom(period)) setOpen(true); }, [period.preset]);

  const pick = (value) => {
    if (value === 'custom_months') {
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
    <div className="glass-card" style={{ padding: '10px 18px', marginBottom: 20 }}>
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <span style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-3)' }}>
            Period
          </span>

          <Segmented
            ariaLabel="Period"
            value={period.preset}
            onChange={pick}
            options={quickPresets.map((p) => [
              p.value, p.short || p.label,
              p.resolved_label ? `${p.label} — ${p.resolved_label}` : p.label,
            ])}
          />

          <Select
            aria-label="Other periods"
            value={others.some((o) => o.value === period.preset) ? period.preset : ''}
            onChange={(v) => v && pick(v)}
            placeholder={others.some((o) => o.value === period.preset) ? 'Other periods…' : 'More periods…'}
            options={others.map((o) => [
              o.value,
              o.resolved_label && !o.label.includes(o.resolved_label)
                ? `${o.label} (${o.resolved_label})`
                : o.label,
            ])}
            style={{ minWidth: 150, maxWidth: 280 }}
          />

          {scoped && (
            <Button size="sm" variant="ghost" onClick={() => pick('all')} title="Show the whole ledger">
              Clear
            </Button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text)' }}>{label}</span>
          {resolved && (
            <span className="badge badge-accent" style={{ fontSize: 11 }}>
              {resolved.basis === 'accounting'
                ? `${resolved.months ? `${resolved.months} mo · ` : ''}Accounting month`
                : 'Transaction date'}
            </span>
          )}
        </div>
      </div>

      {open && isCustom(period) && (
        <div className="flex items-center gap-3 flex-wrap" style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
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
              <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
                Whole accounting months
              </span>
            </>
          ) : (
            <>
              <input type="date" aria-label="First date" value={period.start || ''}
                min={earliest ? `${earliest}-01` : undefined}
                onChange={(e) => setPeriod({ ...period, start: e.target.value })}
                style={{ padding: '4px 8px', borderRadius: 'var(--r-xs)', border: '1px solid var(--line)' }}
              />
              <Icon name="arrow-right" size={13} />
              <input type="date" aria-label="Last date" value={period.end || ''}
                onChange={(e) => setPeriod({ ...period, end: e.target.value })}
                style={{ padding: '4px 8px', borderRadius: 'var(--r-xs)', border: '1px solid var(--line)' }}
              />
              <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
                Exact transaction dates
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function PeriodEmpty({ available }) {
  const { label, setPeriod } = usePeriod();
  const latest = available?.latest;
  return (
    <div className="empty">
      <div style={{
        width: 44, height: 44, borderRadius: 'var(--r-lg)',
        background: 'var(--surface-2)', border: '1px solid var(--line)',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        marginBottom: 12, color: 'var(--text-3)',
      }}>
        <Icon name="calendar" size={20} />
      </div>
      <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>Nothing counted in {label}</h3>
      <p style={{ color: 'var(--text-2)', maxWidth: 460, margin: '0 auto 16px', fontSize: 13.5 }}>
        {latest
          ? 'Either no statement covering it has been parsed, or the period is outside what has been imported.'
          : 'No statements have been analysed yet.'}
      </p>
      {latest && (
        <div className="flex items-center justify-center gap-3">
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
