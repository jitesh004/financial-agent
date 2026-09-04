import React, { useMemo, useState } from 'react';
import { Chip } from '../ui';
import { dateLabel, money } from '../../lib';

/* The pieces the Position tables are built from.

   Two of them carry the ideas that make this screen different from a form.

   `Cell` edits in place. A position is corrected, not filled in - somebody
   opens it, sees that one balance is wrong, fixes that balance and leaves.
   A modal per row would make the five-minute job a twenty-minute one.

   `Aged` shows both figures at once: what was attested, and what it must be
   today. Showing only the second would hide where it came from; showing only
   the first would be quoting a number that stopped being true months ago. */

export const KIND_LABEL = {
  loan: 'Loan', card: 'Card', account: 'Account',
  investment: 'Investment', other: 'Other',
};

/* Sorting that works on a column of mixed types.

   Nulls always sink, whichever way the sort runs. A blank credit limit is not
   "the smallest limit" - it is a limit nobody has recorded, and floating it
   to the top of an ascending sort would put the rows with the least
   information where the eye goes first. */
export function useSorted(rows, initial = { key: 'label', dir: 'asc' }) {
  const [sort, setSort] = useState(initial);

  const sorted = useMemo(() => {
    const { key, dir } = sort;
    const sign = dir === 'desc' ? -1 : 1;
    return [...rows].sort((a, b) => {
      const x = a[key];
      const y = b[key];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * sign;
      return String(x).localeCompare(String(y), undefined,
        { numeric: true, sensitivity: 'base' }) * sign;
    });
  }, [rows, sort]);

  const by = (key) => setSort((prev) => ({
    key,
    // Clicking a new column starts ascending; clicking the current one
    // flips. Anything else means you cannot tell which way it is sorted
    // without reading the arrow.
    dir: prev.key === key && prev.dir === 'asc' ? 'desc' : 'asc',
  }));

  return { sorted, sort, by };
}

export function SortHeader({ label, field, sort, onSort, align = 'left',
  title }) {
  const active = sort.key === field;
  return (
    <th className={align === 'right' ? 'right' : ''} title={title}>
      <button
        type="button"
        onClick={() => onSort(field)}
        style={{
          background: 'none', border: 0, padding: 0, cursor: 'pointer',
          font: 'inherit', color: active ? 'var(--text)' : 'inherit',
          fontWeight: active ? 700 : 'inherit',
        }}
      >
        {label}
        <span style={{ opacity: active ? 1 : 0.25, marginLeft: 4 }}>
          {active && sort.dir === 'desc' ? '▾' : '▴'}
        </span>
      </button>
    </th>
  );
}

/* One editable field.

   Committed on blur and on Enter, abandoned on Escape. Not on every
   keystroke: a partial number is a real number to the server, and saving
   "40" on the way to "400000" would briefly make the position wrong and,
   worse, would re-derive the rate from it. */
export function Cell({ value, onSave, type = 'text', suffix, placeholder,
  width = 110, align = 'right' }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  function begin() {
    setDraft(value == null ? '' : String(value));
    setEditing(true);
  }

  function commit() {
    setEditing(false);
    const next = draft.trim();
    const before = value == null ? '' : String(value);
    if (next === before) return;
    onSave(next === '' ? null : next);
  }

  if (editing) {
    return (
      <input
        autoFocus
        type={type === 'date' ? 'date' : 'text'}
        inputMode={type === 'number' ? 'decimal' : undefined}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit();
          if (e.key === 'Escape') setEditing(false);
        }}
        style={{ width, fontSize: 13, textAlign: align }}
      />
    );
  }

  const empty = value == null || value === '';
  return (
    <button
      type="button"
      onClick={begin}
      title="Click to edit"
      style={{
        background: 'none', border: 0, borderBottom: '1px dashed transparent',
        padding: '1px 2px', cursor: 'text', font: 'inherit', width,
        textAlign: align, color: empty ? 'var(--text-3)' : 'inherit',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderBottomColor = 'var(--border-strong)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderBottomColor = 'transparent';
      }}
    >
      {empty ? (placeholder || '—')
        : type === 'money' ? money(Number(value))
          : type === 'date' ? dateLabel(value)
            : `${value}${suffix || ''}`}
    </button>
  );
}

/* A figure that has aged, and the baseline it aged from.

   The whole design in one component: the big number is what the balance must
   be today, the small one under it is what was actually confirmed and when.
   Where the two are the same - reviewed today, or a kind that does not age -
   only one is shown, because repeating it would imply a change that did not
   happen. */
export function Aged({ item }) {
  const now = item.outstanding;
  const was = item.attested_outstanding;
  const moved = was != null && now != null && Math.abs(was - now) >= 1;
  return (
    <div style={{ textAlign: 'right' }}>
      <div className="num" style={{ fontWeight: 600 }}>
        {now == null ? '—' : money(now)}
      </div>
      {moved && (
        <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
          from {money(was)}
          {item.emis_since_review
            ? ` · ${item.emis_since_review} EMI${item.emis_since_review > 1 ? 's' : ''} on`
            : ''}
        </div>
      )}
    </div>
  );
}

/* Why this row's number is what it is.

   Every figure in this app can be traced to the document it came from. An
   attested one has no document, so it carries a sentence instead - and the
   sentence is not optional, because a number a person typed and a number a
   bank printed must never look the same. */
export function Basis({ item }) {
  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center',
      flexWrap: 'wrap' }}
    >
      <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{item.basis}</span>
      {item.stale && <Chip tone="warn">needs a look</Chip>}
      {item.drift != null && (
        <Chip tone="neg" title="Your figure and the statements disagree">
          off by {money(Math.abs(item.drift))}
        </Chip>
      )}
      {item.derived?.length > 0 && (
        <Chip title={`Worked out, not confirmed: ${item.derived.join(', ')}`}>
          {item.derived.length} worked out
        </Chip>
      )}
    </div>
  );
}
