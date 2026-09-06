/* Formatting, in one place.
 *
 * Every figure in this app is Indian currency, and Indian digit grouping is
 * 1,23,456 rather than 123,456. Getting that wrong is immediately jarring to
 * the people this is for, so no screen is allowed to call `toLocaleString`
 * itself - it goes through here.
 *
 * `Intl.NumberFormat` instances are expensive to construct and free to reuse,
 * and a transaction table formats a few thousand cells per render, so they are
 * built once at module scope.
 */

const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR',
  minimumFractionDigits: 0, maximumFractionDigits: 0,
});
const inrPrecise = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR',
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});
const plain = new Intl.NumberFormat('en-IN');

export const DASH = '—';

export function money(value, precise = false) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return DASH;
  return (precise ? inrPrecise : inr).format(Number(value));
}

/* A count, with Indian grouping. Not money, so it carries no rupee sign -
   which matters, because a bare number handed to `money` renders a tally of
   293 rows as "₹293" directly above a real rupee figure. */
export function count(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return DASH;
  return plain.format(Number(value));
}

/* Compact, in Indian units. A crore axis labelled "12,00,00,000" is
   unreadable; "₹12Cr" is not. */
export function compact(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return DASH;
  const n = Math.abs(Number(value));
  const sign = Number(value) < 0 ? '-' : '';
  if (n >= 1e7) return `${sign}₹${(n / 1e7).toFixed(n >= 1e8 ? 0 : 1)}Cr`;
  if (n >= 1e5) return `${sign}₹${(n / 1e5).toFixed(n >= 1e6 ? 0 : 1)}L`;
  if (n >= 1e3) return `${sign}₹${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return `${sign}₹${n.toFixed(0)}`;
}

/* The same scale without the currency mark, for an axis whose measure is a
   count rather than an amount. */
export function compactNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return DASH;
  return compact(value).replace('₹', '');
}

export function pct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return DASH;
  return `${Number(value).toFixed(digits)}%`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/* "2026-08" -> "Aug 26". Short, because it is mostly an axis tick where every
   character competes for width. */
export function monthLabel(key) {
  if (!key) return '';
  const [y, m] = String(key).split('-');
  return `${MONTHS[Number(m) - 1] || m} ${String(y).slice(2)}`;
}

/* "2026-08" -> "Aug 2026". For prose and for the period control, whose labels
   have to read the same as the server's - a window announced as "Aug 26 – Nov
   26" in one place and "Aug 2026 – Nov 2026" in another looks like two
   different windows. */
export function monthLabelLong(key) {
  if (!key) return '';
  const [y, m] = String(key).split('-');
  return `${MONTHS[Number(m) - 1] || m} ${y}`;
}

export function dateLabel(iso) {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' });
}

export function dateLabelLong(iso) {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
}

/* "3 days ago". Used where the age of an answer matters more than its date -
   an agent run from this morning is worth reading, one from three weeks ago
   is worth re-running. */
export function ago(iso) {
  if (!iso) return '';
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return '';
  const days = Math.floor((Date.now() - then.getTime()) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days} days ago`;
  return dateLabel(iso);
}

export function titleCase(text) {
  if (!text) return '';
  return String(text).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function bytes(n) {
  if (!n) return DASH;
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024).toFixed(0)} KB`;
}

export function duration(seconds) {
  if (seconds == null) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

/* "1 file" / "3 files", without every call site spelling out the ternary. */
export function plural(n, one, many) {
  return `${count(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;
}

export const today = () => new Date().toISOString().slice(0, 10);

/* Twelve categorical slots, read from CSS rather than written as hex here -
   which is what makes a chart follow the theme without being re-rendered. */
export const SERIES = Array.from({ length: 12 }, (_, i) => `var(--c${i + 1})`);
export const colorFor = (i) => SERIES[((i % 12) + 12) % 12];

/* The flow roles the spending breakdown is a sum over.
 *
 * `analysis.by_category` is built from expense rows plus the contra roles
 * that net against them, so a drill-down from a category bar has to ask for
 * the same set or the panel contradicts the figure that opened it: an EMI bar
 * reading 3,87,864 across 19 rows opened a drawer headed 7,31,327 across 27,
 * the extra being the same instalments seen again as transfer legs. */
export const SPEND_ROLES = 'expense,refund,claim_settlement';

/* CSV, built here so every table exports the same way. Quoting is not
   optional: transaction descriptions routinely contain commas and quotes, and
   one unescaped character silently shifts every later column. */
export function toCsv(rows, columns) {
  const esc = (v) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = columns.map(([, label]) => esc(label)).join(',');
  const body = rows.map((r) => columns
    .map(([key]) => esc(typeof key === 'function' ? key(r) : r[key]))
    .join(','));
  return [head, ...body].join('\n');
}

export function downloadCsv(filename, csv) {
  const blob = new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function slug(text) {
  return String(text || 'export').replace(/[^\w-]+/g, '-').replace(/^-|-$/g, '').toLowerCase();
}
