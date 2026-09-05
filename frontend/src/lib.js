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

/* A plain count with Indian grouping - 1,23,456, not 123,456. Not money, so
   it carries no rupee sign. Three components each spelled out the locale. */
export function count(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return Number(value).toLocaleString('en-IN');
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

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/* "2026-08" -> "Aug 26". Short because it is mostly an axis tick, where
   every character is competing for width. */
export function monthLabel(key) {
  if (!key) return '';
  const [y, m] = String(key).split('-');
  return `${MONTH_NAMES[Number(m) - 1] || m} ${String(y).slice(2)}`;
}

/* "2026-08" -> "Aug 2026". For prose and for the period control, whose
   labels have to read the same as the server's - see the period labels in
   backend/app/analytics/periods.py. A window announced as "Aug 26 – Nov 26"
   in one place and "Aug 2026 – Nov 2026" in another looks like two windows. */
export function monthLabelLong(key) {
  if (!key) return '';
  const [y, m] = String(key).split('-');
  return `${MONTH_NAMES[Number(m) - 1] || m} ${y}`;
}

export function dateLabel(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' });
}

/* The flow roles the spending breakdown is a sum over.
 *
 * `analysis.by_category` is built from `spend_txns + offset_txns` - rows whose
 * role is EXPENSE, plus the contra roles that net against them. Clicking a
 * bar has to ask for the same rows, or the panel contradicts the figure that
 * opened it: an EMI bar reading 3,87,864 across 19 transactions opened a
 * drawer headed 7,31,327 across 27, the extra being the same instalments seen
 * again as transfer legs.
 *
 * Sent as a `flow_role` filter, which /api/transactions already accepts as a
 * comma-separated list. */
export const SPEND_ROLES = 'expense,refund,claim_settlement';

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

/* Query string, minus anything unset - so an absent filter is absent rather
   than sent as the string "undefined". Written once; it was spelled out
   separately at every call site that needed it. */
function query(params = {}) {
  return new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== '' && v != null),
  ).toString();
}

/* Called when the server says the session is gone. Set by the auth provider
   so a 401 anywhere in the app lands on the sign-in screen instead of
   surfacing as "401 Unauthorized" inside whatever panel happened to ask. */
let onUnauthorized = null;
export function setUnauthorizedHandler(handler) { onUnauthorized = handler; }

async function request(path, options = {}) {
  /* same-origin so the session cookie travels; the API is reached through the
     dev server's proxy and through nginx in production, never cross-origin. */
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  if (response.status === 401 && !path.startsWith('/api/auth/')) {
    onUnauthorized?.();
  }
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

  /* Every period the app offers, already resolved to months by the server,
     plus which accounting months the ledger actually holds rows in. Resolved
     there rather than here so "last 3 months" has one definition - see
     backend/app/analytics/periods.py. */
  periods: () => request('/api/periods'),

  /* The dashboard's figures for ONE period, recomputed from stored rows.
     Separate from `dashboard()` because that one carries the narrative and
     the transfer report, neither of which is re-derivable per period without
     re-running the model. */
  analysis: (params = {}) => request(`/api/analysis?${query(params)}`),

  /* What a month costs and what it leaves: commitments, what varies, and the
     arithmetic between them. Computed from stored rows - see
     backend/app/analytics/budget.py. */
  budget: (params = {}) => request(`/api/budget?${query(params)}`),

  /* The operator's view: who is on this deployment and how much they use it.
     404s for everyone whose address is not in FA_ADMIN_EMAILS - see
     backend/app/api/admin_routes.py, which also explains why it reports
     volumes and never amounts. */
  adminOverview: (detail = true) =>
    request(`/api/admin/overview?detail=${detail ? 'true' : 'false'}`),
  run: (id) => request(`/api/runs/${id}`),
  accounts: () => request('/api/accounts'),
  categories: () => request('/api/categories'),
  statements: () => request('/api/statements'),

  transactions: (params = {}) => request(`/api/transactions?${query(params)}`),

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

/* ---------- Demo mode ----------

   Points the app at a generated workspace instead of the real ledger, so it
   can be demonstrated without showing anybody a real financial history. The
   switch decides which account's rows are read; it never moves a row. */
api.demo = () => request('/api/settings/demo');
api.setDemo = (enabled) => jsonPost('/api/settings/demo', { enabled });
api.rebuildDemo = () => jsonPost('/api/settings/demo/rebuild');

/* Flip the switch and start the app again from scratch.
 *
 * The reload is the point. Turning demo mode on or off changes which account
 * EVERY panel reads, and no single place owns all of that state - the
 * dashboard, the period list, Recurring, Budget, the Admin view and each open
 * drill-down each hold their own copy. Re-reading the session is not enough:
 * doing only that left the Overview summary reporting the real ledger's
 * twelve months over the demo workspace's figures. A screen that mixes the
 * two ledgers is the one outcome this switch must never produce, and a reload
 * is the only way to rule it out rather than chase it panel by panel. */
export async function switchDemo(enabled) {
  await api.setDemo(enabled);
  window.location.reload();
}

api.profile = () => request('/api/profile');
api.saveProfile = (profile) => request('/api/profile', {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(profile),
});


/* ---------- Signing in ---------- */

api.session = () => request('/api/auth/session');
api.authConfig = () => request('/api/auth/config');
api.logout = () => jsonPost('/api/auth/logout');
api.logoutEverywhere = () => jsonPost('/api/auth/logout-all');
api.activeSessions = () => request('/api/auth/sessions');
api.deleteAccount = (confirmEmail) =>
  jsonPost('/api/auth/delete-account', { confirm_email: confirmEmail });

/* ---------- Onboarding ---------- */

api.onboarding = () => request('/api/onboarding');
api.onboardingStep = (step) => jsonPost('/api/onboarding/step', { step });
api.onboardingComplete = () => jsonPost('/api/onboarding/complete');
api.onboardingReopen = () => jsonPost('/api/onboarding/reopen');


/* ---------- Gmail (staged, job-based) ---------- */

const jsonPost = (path, body) => request(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});

/* The rules the app runs on you. Read-only: these live in code, reviewed and
   tested, and an editable copy would be a second source of truth for every
   one of them. */
api.rules = () => request('/api/rules');
/* What the app decided about ONE row - its category rule, why it is money
   in or out, and the transfer group it belongs to. */
api.explainTransaction = (id) => request(`/api/rules/explain/${id}`);
api.testRules = (example) => jsonPost('/api/rules/test', example);

api.gmailStatus = () => request('/api/gmail/status');
/* A full-page navigation, not a fetch. Consent happens on Google's own page,
   and the server has no browser of its own to open one in - which is what the
   old POST /api/gmail/connect quietly assumed. `redirect_to` is where Google
   sends the browser after the grant. */
api.gmailConnect = (redirectTo = window.location.pathname) => {
  window.location.href =
    `/api/auth/google/start?purpose=gmail&redirect_to=${encodeURIComponent(redirectTo)}`;
};
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

api.jobs = (params = {}) => request(`/api/jobs?${query(params)}`);
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


/* ---------- Position ----------
   The one screen whose figures the USER asserts rather than a document
   declaring them. Everything here is a partial patch: correcting an
   outstanding balance must never disturb a label fixed earlier, and sending
   an explicit null is how a field is unset as distinct from left alone.

   `reviewItem` is deliberately not a patch. Moving the review date is the act
   that says "I have looked at this and it is right", and it resets the
   roll-forward - so it must not happen as a side effect of fixing a typo. */

api.position = (includeArchived = false) =>
  request(`/api/position?include_archived=${includeArchived ? 'true' : 'false'}`);
api.positionMappable = () => request('/api/position/mappable');
api.seedPosition = () => jsonPost('/api/position/seed');
api.addPositionItem = (fields) => jsonPost('/api/position/items', fields);
api.updatePositionItem = (id, fields) =>
  jsonPatch(`/api/position/items/${id}`, fields);
api.reviewPositionItem = (id, reviewedOn) =>
  jsonPost(`/api/position/items/${id}/review`, { reviewed_on: reviewedOn });
api.deletePositionItem = (id, permanent = false) =>
  request(`/api/position/items/${id}?permanent=${permanent ? 'true' : 'false'}`,
    { method: 'DELETE' });
api.reviewPosition = (body) => jsonPost('/api/position/review', body);
api.positionSnapshots = () => request('/api/position/snapshots');
api.positionSnapshot = (id) => request(`/api/position/snapshots/${id}`);
api.deletePositionSnapshot = (id) =>
  request(`/api/position/snapshots/${id}`, { method: 'DELETE' });


/* ---------- Agents ----------
   A run is a JOB, not a request: several model round trips with tool
   execution between them takes tens of seconds, and an HTTP request held
   open that long dies to a proxy timeout - taking the analysis, which is the
   expensive part, with it. So `runAgent` hands back a job id and the screen
   watches it with `watchJob` the same way an import is watched. */

api.agents = () => request('/api/agents');
api.runAgent = (key, question = '') =>
  jsonPost(`/api/agents/${key}/run`, { question });
api.agentRuns = (key, limit = 20) =>
  request(`/api/agents/${key}/runs?limit=${limit}`);
/* The transcript is every tool call and every result the agent read, which is
   what makes its figures checkable - and easily the largest thing in the row,
   so it is only fetched when somebody opens it. */
api.agentRun = (id, { transcript = false } = {}) =>
  request(`/api/agents/runs/${id}?transcript=${transcript ? 'true' : 'false'}`);
api.deleteAgentRun = (id) =>
  request(`/api/agents/runs/${id}`, { method: 'DELETE' });


/* ---------- Settings, and the run that spends money ---------- */

api.settings = () => request('/api/settings');
api.saveSettings = (body) => request('/api/settings', {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});
/* Returns a job id: categorising a few hundred rows is slow enough to watch,
   and the same job machinery reports it as any import does. */
api.runCategorize = () => jsonPost('/api/settings/categorize');

/* ---------- Staging: read, reviewed, and only then counted ----------
 *
 * Everything a scan or an upload produces goes here first. None of it is in
 * the ledger, no tab reads it, and `stagingProcess` is the only call in this
 * file that changes a single figure anywhere in the app. */

api.stagingReview = () => request('/api/staging/review');
api.stagingSelect = (body) => request('/api/staging/select', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});
api.stagingSections = () => request('/api/staging/sections');
api.stagingForget = (intent) => jsonPost(
  intent ? `/api/staging/forget?intent=${encodeURIComponent(intent)}`
         : '/api/staging/forget');
api.stagingParse = (intent) => jsonPost(
  intent ? `/api/staging/parse?intent=${encodeURIComponent(intent)}`
         : '/api/staging/parse');
api.stagingProcess = () => jsonPost('/api/staging/process');
api.stagingRemove = (ids) => request('/api/staging/files', {
  method: 'DELETE',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ids, include: false }),
});

/* What a scan found, recorded in staging. Parses nothing. */
api.stageScanResults = (body) => request('/api/staging/scan-results', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});
