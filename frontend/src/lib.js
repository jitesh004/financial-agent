/* Shared formatting and API helpers. */

/* Indian digit grouping (1,23,456) rather than Western (123,456). Getting this
   wrong is immediately jarring to the people this app is for. */
const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR',
  minimumFractionDigits: 0, maximumFractionDigits: 0,
});
const inrPrecise = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR',
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});

export function money(value, precise = false) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return (precise ? inrPrecise : inr).format(value);
}

/* Compact form for axis ticks and tight tiles, in Indian units. A crore axis
   labelled "12,00,00,000" is unreadable; "₹12Cr" is not. */
export function compact(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = value < 0 ? '-' : '';
  const n = Math.abs(value);
  if (n >= 1e7) return `${sign}₹${(n / 1e7).toFixed(n >= 1e8 ? 0 : 1)}Cr`;
  if (n >= 1e5) return `${sign}₹${(n / 1e5).toFixed(n >= 1e6 ? 0 : 1)}L`;
  if (n >= 1e3) return `${sign}₹${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return `${sign}₹${n.toFixed(0)}`;
}

export function pct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${value.toFixed(digits)}%`;
}

export function monthLabel(key) {
  if (!key) return '';
  const [y, m] = key.split('-');
  const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${names[Number(m) - 1] || m} ${String(y).slice(2)}`;
}

export function dateLabel(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' });
}

export function titleCase(text) {
  if (!text) return '';
  return String(text).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/* Twelve categorical slots, read from CSS so charts follow the active theme
   instead of hard-coding hex values that only work in one of them. */
export const SERIES_COLORS = Array.from({ length: 12 }, (_, i) => `var(--c${i + 1})`);

export function colorFor(index) {
  return SERIES_COLORS[index % SERIES_COLORS.length];
}

/* ---------- API ---------- */

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail?.message || body.detail || body.message || detail;
    } catch { /* non-JSON error body; keep the status line */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return response.json();
}

export const api = {
  health: () => request('/api/health'),
  dashboard: () => request('/api/dashboard'),
  run: (id) => request(`/api/runs/${id}`),
  accounts: () => request('/api/accounts'),
  categories: () => request('/api/categories'),
  statements: () => request('/api/statements'),

  transactions: (params = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null),
    );
    return request(`/api/transactions?${query}`);
  },

  upload: (files, { useLlm = true, horizonMonths = 6 } = {}) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f));
    form.append('use_llm', String(useLlm));
    form.append('horizon_months', String(horizonMonths));
    return request('/api/upload', { method: 'POST', body: form });
  },

  recategorize: (id, category) => request(`/api/transactions/${id}/category`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category }),
  }),

  reanalyze: () => request('/api/reanalyze', { method: 'POST' }),
  reset: () => request('/api/reset', { method: 'POST' }),

  files: () => request('/api/files'),
  fileTransactions: (id) => request(`/api/files/${id}/transactions`),
  retryFile: (id, password) => request(`/api/files/${id}/retry`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(password ? { password } : {}),
  }),

  coverage: () => request('/api/coverage'),
  fetchMonth: (accountId, month) => jsonPost(`/api/coverage/${accountId}/${month}/fetch`),
  fetchAllMissing: () => jsonPost('/api/coverage/fetch-all-missing'),
};

api.profile = () => request('/api/profile');
api.saveProfile = (profile) => request('/api/profile', {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(profile),
});


/* ---------- Gmail (staged, job-based) ---------- */

const jsonPost = (path, body) => request(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});

api.gmailStatus = () => request('/api/gmail/status');
api.gmailConnect = () => jsonPost('/api/gmail/connect');
api.gmailDisconnect = () => jsonPost('/api/gmail/disconnect');
api.gmailPeriods = () => request('/api/gmail/periods');
api.gmailIgnored = () => request('/api/gmail/ignored');
api.gmailSetIgnored = (senders) => request('/api/gmail/ignored', {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ excluded_senders: senders }),
});
api.gmailScan = (maxMessages = 400, months = null) => jsonPost(
  `/api/gmail/scan?max_messages=${maxMessages}`
  + (months ? `&months=${months}` : ''),
);
api.gmailDownload = (attachments) => jsonPost('/api/gmail/download', { attachments });
api.gmailProcess = (files) => jsonPost('/api/gmail/process', { files });
api.gmailJob = (id) => request(`/api/gmail/jobs/${id}`);
api.gmailCancel = (id) => jsonPost(`/api/gmail/jobs/${id}/cancel`);

/* Poll a job until it finishes, calling onTick with each snapshot so the UI can
   render live progress. Resolves with the finished job. */
export async function pollJob(jobId, onTick, intervalMs = 700) {
  for (;;) {
    const job = await api.gmailJob(jobId);
    onTick?.(job);
    if (job.status === 'complete') return job;
    if (job.status === 'failed') {
      throw new Error(job.errors?.join('; ') || 'Job failed.');
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

export function formatBytes(bytes) {
  if (!bytes) return '—';
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

export function formatDuration(seconds) {
  if (seconds == null) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}
