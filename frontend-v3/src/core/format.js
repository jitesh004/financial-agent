/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Number, Currency, Date & CSV Formatting
   ──────────────────────────────────────────────────────────────────────── */

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

export function count(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return DASH;
  return plain.format(Number(value));
}

export function compact(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return DASH;
  const n = Math.abs(Number(value));
  const sign = Number(value) < 0 ? '-' : '';
  if (n >= 1e7) return `${sign}₹${(n / 1e7).toFixed(n >= 1e8 ? 0 : 1)}Cr`;
  if (n >= 1e5) return `${sign}₹${(n / 1e5).toFixed(n >= 1e6 ? 0 : 1)}L`;
  if (n >= 1e3) return `${sign}₹${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return `${sign}₹${n.toFixed(0)}`;
}

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

export function monthLabel(key) {
  if (!key) return '';
  const [y, m] = String(key).split('-');
  return `${MONTHS[Number(m) - 1] || m} ${String(y).slice(2)}`;
}

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

export function today() {
  return new Date().toISOString().slice(0, 10);
}

export function stampLabel(value) {
  if (!value) return DASH;
  const d = new Date(String(value).replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return String(value);
  return `${dateLabel(d.toISOString())}, ${d.toLocaleTimeString('en-IN', {
    hour: '2-digit', minute: '2-digit', hour12: false })}`;
}

export function ago(iso) {
  if (!iso) return DASH;
  const ms = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(ms / 1000);
  if (sec < 45) return 'just now';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  const mo = Math.floor(days / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.floor(mo / 12)}y ago`;
}

export function duration(ms) {
  if (!ms && ms !== 0) return DASH;
  const s = Math.round(Number(ms) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

export function plural(n, one, many) {
  return `${count(n)} ${Number(n) === 1 ? one : (many || `${one}s`)}`;
}

export function titleCase(s) {
  if (!s) return '';
  return String(s)
    .replace(/[_-]+/g, ' ')
    .replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

export const SPEND_ROLES = 'expense,refund,claim_settlement';

export const PALETTE = [
  'var(--c1)', 'var(--c2)', 'var(--c3)', 'var(--c4)', 'var(--c5)', 'var(--c6)',
  'var(--c7)', 'var(--c8)', 'var(--c9)', 'var(--c10)', 'var(--c11)', 'var(--c12)',
];

export function colorFor(index) {
  return PALETTE[Math.abs(Number(index || 0)) % PALETTE.length];
}

export function bytes(b) {
  if (b == null || Number.isNaN(Number(b))) return DASH;
  const n = Number(b);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

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
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
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
