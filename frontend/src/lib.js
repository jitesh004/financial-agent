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

  // PATCH /api/transactions/{id} - the per-field endpoint. This used to
  // target .../category, which no longer exists, so every category change
  // from the transactions table was 404ing.
  recategorize: (id, category) => api.updateTransaction(id, { category }),

  reanalyze: (months) => request(`/api/reanalyze${months ? `?months=${months}` : ''}`, { method: 'POST' }),
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

  // Escape hatch for anything not yet given a named method.
  request,
};

const jsonPatch = (path, body) => request(path, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body ?? {}),
});

/* ---------- Editing a transaction ---------- */

// Every field the user can override. Sent as a partial patch, so changing a
// note never disturbs a category correction made earlier.
api.updateTransaction = (id, fields) => jsonPatch(`/api/transactions/${id}`, fields);
// The request model names this `txn_ids`; sending `ids` silently updated
// nothing, because the field was simply absent from the parsed payload.
api.bulkUpdate = (txn_ids, fields) =>
  jsonPatch('/api/transactions/bulk', { txn_ids, ...fields });
api.splitTransaction = (id, parts) =>
  jsonPost(`/api/transactions/${id}/split`, { parts });
api.claimTransaction = (id, body) =>
  jsonPost(`/api/transactions/${id}/claim`, body);

/* ---------- Review, claims, recurring ---------- */

api.reviewQueue = (params = {}) =>
  api.transactions({ needs_review: true, limit: 200, ...params });
api.claims = (status) => request(`/api/claims${status ? `?status=${status}` : ''}`);
api.settleClaim = (id, body) => jsonPost(`/api/claims/${id}/settle`, body);

api.recurring = () => request('/api/recurring');
api.updateSeries = (id, fields) => jsonPatch(`/api/recurring/${id}`, fields);
api.deleteSeries = (id) => request(`/api/recurring/${id}`, { method: 'DELETE' });

/* ---------- Categories ---------- */

api.addCategory = (name) => jsonPost('/api/categories', { name });
api.deleteCategory = (name) =>
  request(`/api/categories/${encodeURIComponent(name)}`, { method: 'DELETE' });

/* ---------- Workflow & data lifecycle ---------- */

api.workflow = () => request('/api/workflow');
api.inventory = () => request('/api/data/inventory');
api.previewData = (scope) => request(`/api/data/preview/${scope}`);
api.clearData = (scope, confirm) =>
  jsonPost(`/api/data/clear/${scope}`, confirm ? { confirm } : {});
api.restoreSnapshot = (name) => jsonPost('/api/data/restore', { name });
api.deleteSnapshot = (name) => request(`/api/data/snapshots/${name}`, { method: 'DELETE' });

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
api.gmailIntents = () => request('/api/gmail/intents');
api.gmailScan = (maxMessages = 400, months = null, intent = 'statement') => jsonPost(
  `/api/gmail/scan?max_messages=${maxMessages}`
  + (months ? `&months=${months}` : '')
  + `&intent=${intent}`,
);
/* Alerts have no download stage - the amount is in the body, so the scan has
   already read everything. This writes the chosen ones into the ledger. */
api.gmailImportAlerts = (messageIds, scanJobId) =>
  jsonPost('/api/gmail/alerts/import', {
    message_ids: messageIds, scan_job_id: scanJobId,
  });
/* `thenProcess` chains the parse on the SERVER when the download finishes.
   The chain used to be two awaits in the browser, so closing the tab between
   them left a pile of downloaded files that nothing ever parsed. */
api.gmailDownload = (attachments, { thenProcess = false, useLlm = false } = {}) =>
  jsonPost('/api/gmail/download', {
    attachments, then_process: thenProcess, use_llm: useLlm,
  });
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



api.querySchema = () => request('/api/query/schema');

/* `board` carries the dashboard's own date range and filters. The server
   merges them into the widget's query, so the same widget can be re-cut by
   the board without its saved definition ever being rewritten. */
api.runQuery = (query, board) => jsonPost('/api/query', { query, board });

api.boards = () => request('/api/dashboards');
api.board = (id) => request(`/api/dashboards/${id}`);
api.boardTemplates = () => request('/api/dashboards/templates');
api.createBoard = (body) => jsonPost('/api/dashboards', body);
api.updateBoard = (id, body) => request(`/api/dashboards/${id}`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});
api.deleteBoard = (id) => request(`/api/dashboards/${id}`, { method: 'DELETE' });
api.duplicateBoard = (id, name) => jsonPost(`/api/dashboards/${id}/duplicate`, { name });
api.runBoard = (id, board) => jsonPost(`/api/dashboards/${id}/run`, { board });
api.importBoard = (dashboard) => jsonPost('/api/dashboards/import', { dashboard });

api.createWidget = (dashboardId, widget) =>
  jsonPost(`/api/dashboards/${dashboardId}/widgets`, widget);
api.updateWidget = (dashboardId, widgetId, widget) =>
  request(`/api/dashboards/${dashboardId}/widgets/${widgetId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(widget),
  });
api.deleteWidget = (dashboardId, widgetId) =>
  request(`/api/dashboards/${dashboardId}/widgets/${widgetId}`, { method: 'DELETE' });
api.saveLayout = (dashboardId, layout) =>
  request(`/api/dashboards/${dashboardId}/layout`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ layout }),
  });

/* Downloads go through fetch rather than a plain link so an error surfaces as
   an error, instead of the browser silently saving a JSON error body as if it
   were the file the user asked for. */
async function download(path, options, filename) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`Export failed: ${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

api.exportQueryCsv = (query, board, filename = 'export') => download(
  '/api/query/export',
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, board, filename }),
  },
  `${filename}.csv`,
);

api.exportBoard = (id, name = 'dashboard') =>
  download(`/api/dashboards/${id}/export`, {}, `${name}.json`);


/* ---------- Background jobs ----------
   Kind-agnostic, unlike the /api/gmail/jobs routes these sit beside: a job is
   created by the file registry's retry as well as by the mailbox wizard.

   `activeJobs` is the one that matters for reconnecting. It answers "is work
   happening right now?" without the caller having kept a job id, which is what
   lets progress survive closing the tab - the UI rejoins work in flight
   instead of assuming anything it was not watching had stopped. */

api.jobs = (params = {}) => {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== '' && v != null),
  );
  return request(`/api/jobs?${query}`);
};
api.activeJobs = () => api.jobs({ active: true });
api.job = (id) => request(`/api/jobs/${id}`);
api.cancelJob = (id) => jsonPost(`/api/jobs/${id}/cancel`);
/* Re-dispatches only what the interrupted run had not finished. */
api.resumeJob = (id) => jsonPost(`/api/gmail/jobs/${id}/resume`);

/* Poll a job until it stops. Unlike pollJob above this tolerates a job that
   was interrupted - a server restart mid-scan resolves rather than hanging
   forever waiting for a status that will never arrive. */
export async function watchJob(jobId, onTick, intervalMs = 700) {
  for (;;) {
    const job = await api.job(jobId);
    onTick?.(job);
    if (!job.active) return job;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
