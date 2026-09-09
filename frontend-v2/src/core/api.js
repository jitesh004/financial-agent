/* The API, as one typed surface.
 *
 * Every request in the app goes through `request` below, which means there is
 * exactly one place that knows how a session travels, what an error body looks
 * like, and what happens when the server says the session is gone.
 *
 * Same-origin, always: the API is reached through the dev server's proxy and
 * through nginx in production, never cross-origin, so the session cookie
 * travels and no financial data is ever subject to a CORS preflight.
 */

/* Called when the server says the session has ended. Set by the auth provider
   so a 401 anywhere lands on the sign-in screen rather than surfacing as
   "401 Unauthorized" inside whichever panel happened to ask. */
let onUnauthorized = null;
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

/* A query string minus anything unset, so an absent filter is absent rather
   than sent as the literal string "undefined". */
export function query(params = {}) {
  return new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== '' && v != null),
  ).toString();
}

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

export async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { credentials: 'same-origin', ...options });
  } catch (e) {
    // A network failure is not a server error, and saying "Failed to fetch"
    // to somebody whose wifi dropped explains nothing.
    throw new ApiError('The server could not be reached. Check your connection.', 0, null);
  }

  if (response.status === 401 && !path.startsWith('/api/auth/')) onUnauthorized?.();

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    let body = null;
    try {
      body = await response.json();
      detail = body.detail?.message || body.detail || body.message || detail;
    } catch { /* a non-JSON error body; the status line stands */ }
    throw new ApiError(
      typeof detail === 'string' ? detail : JSON.stringify(detail),
      response.status, body,
    );
  }

  if (response.status === 204) return null;
  return response.json();
}

const get = (path) => request(path);
const post = (path, body) => request(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});
const put = (path, body) => request(path, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body ?? {}),
});
const patch = (path, body) => request(path, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body ?? {}),
});
const del = (path, body) => request(path, {
  method: 'DELETE',
  ...(body === undefined ? {} : {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }),
});

/* Downloads go through fetch rather than a plain link, so an error surfaces as
   an error instead of the browser silently saving a JSON error body as if it
   were the file somebody asked for. */
async function download(path, options, filename) {
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  if (!response.ok) throw new ApiError(`Export failed: ${response.status}`, response.status);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export const api = {
  request, get, post, put, patch, del,

  /* ---- who is signed in ---- */
  session: () => get('/api/auth/session'),
  authConfig: () => get('/api/auth/config'),
  logout: () => post('/api/auth/logout'),
  logoutEverywhere: () => post('/api/auth/logout-all'),
  activeSessions: () => get('/api/auth/sessions'),
  deleteAccount: (confirmEmail) => post('/api/auth/delete-account', { confirm_email: confirmEmail }),
  signInUrl: (redirectTo = window.location.pathname) =>
    `/api/auth/google/start?redirect_to=${encodeURIComponent(redirectTo)}`,

  /* ---- first run ---- */
  onboarding: () => get('/api/onboarding'),
  onboardingStep: (step) => post('/api/onboarding/step', { step }),
  onboardingComplete: () => post('/api/onboarding/complete'),
  onboardingReopen: () => post('/api/onboarding/reopen'),

  /* ---- the ledger ---- */
  health: () => get('/api/health'),
  dashboard: () => get('/api/dashboard'),
  /* Every period the app offers, already resolved to months by the server,
     plus which accounting months the ledger holds rows in. Resolved there
     rather than here so "last 3 months" has exactly one definition. */
  periods: () => get('/api/periods'),
  /* The dashboard's figures for ONE period, recomputed from stored rows.
     Separate from `dashboard()` because that one carries the narrative and the
     transfer report, neither of which is re-derivable per period without
     re-running the model. */
  analysis: (params = {}) => get(`/api/analysis?${query(params)}`),
  budget: (params = {}) => get(`/api/budget?${query(params)}`),
  accounts: () => get('/api/accounts'),
  categories: () => get('/api/categories'),
  customCategories: () => get('/api/categories/custom'),
  addCategory: (name) => post('/api/categories', { name }),
  deleteCategory: (name) => del(`/api/categories/${encodeURIComponent(name)}`),
  statements: () => get('/api/statements'),
  workflow: () => get('/api/workflow'),
  reanalyze: (months) => post(`/api/reanalyze${months ? `?months=${months}` : ''}`),

  /* ---- transactions ---- */
  transactions: (params = {}) => get(`/api/transactions?${query(params)}`),
  /* The most rows /api/transactions will return whatever `limit` asks for. A
     caller that wants "as many as I can get" has to know this number, because
     asking for more is answered silently with this many - and a screen that
     believed its own larger limit reported "showing the first 2000 of N" over
     a thousand rows, or quietly showed a page and called it the queue. */
  PAGE_MAX: 1000,
  reviewQueue: (params = {}) =>
    api.transactions({ needs_review: true, limit: api.PAGE_MAX, ...params }),
  updateTransaction: (id, fields) => patch(`/api/transactions/${id}`, fields),
  /* The request model names this `txn_ids`; sending `ids` parses to an empty
     payload and silently updates nothing. */
  bulkUpdate: (txnIds, fields) => patch('/api/transactions/bulk', { txn_ids: txnIds, ...fields }),
  splitTransaction: (id, parts) => post(`/api/transactions/${id}/split`, { parts }),
  claimTransaction: (id, body) => post(`/api/transactions/${id}/claim`, body),
  recategorize: (id, category) => patch(`/api/transactions/${id}`, { category }),

  /* ---- claims, recurring ---- */
  claims: (status) => get(`/api/claims${status ? `?status=${status}` : ''}`),
  settleClaim: (id, body) => post(`/api/claims/${id}/settle`, body),
  recurring: () => get('/api/recurring'),
  updateSeries: (id, fields) => patch(`/api/recurring/${id}`, fields),
  deleteSeries: (id) => del(`/api/recurring/${id}`),

  /* ---- files, coverage ---- */
  files: () => get('/api/files'),
  fileTransactions: (id) => get(`/api/files/${id}/transactions`),
  retryFile: (id, password) => post(`/api/files/${id}/retry`, password ? { password } : {}),
  coverage: () => get('/api/coverage'),
  fetchMonth: (accountId, month) => post(`/api/coverage/${accountId}/${month}/fetch`),
  fetchAllMissing: () => post('/api/coverage/fetch-all-missing'),

  /* ---- data lifecycle ---- */
  inventory: () => get('/api/data/inventory'),
  previewData: (scope) => get(`/api/data/preview/${scope}`),
  clearData: (scope, confirm) => post(`/api/data/clear/${scope}`, confirm ? { confirm } : {}),
  restoreSnapshot: (name) => post('/api/data/restore', { name }),
  deleteSnapshot: (name) => del(`/api/data/snapshots/${name}`),
  reset: () => post('/api/reset'),

  /* ---- profile ---- */
  profile: () => get('/api/profile'),
  saveProfile: (profile) => put('/api/profile', profile),

  /* ---- settings, and the run that spends money ---- */
  settings: () => get('/api/settings'),
  saveSettings: (body) => put('/api/settings', body),
  runCategorize: () => post('/api/settings/categorize'),
  demo: () => get('/api/settings/demo'),
  setDemo: (enabled) => post('/api/settings/demo', { enabled }),
  rebuildDemo: () => post('/api/settings/demo/rebuild'),

  /* ---- the rules the app runs on you ---- */
  rules: () => get('/api/rules'),
  explainTransaction: (id) => get(`/api/rules/explain/${id}`),
  testRules: (example) => post('/api/rules/test', example),

  /* ---- wealth ---- */
  bureau: () => get('/api/bureau'),
  bureauReconciliation: () => get('/api/bureau/reconciliation'),
  matchBureauAccount: (bureauAccountId, accountId, confirmed) =>
    post(`/api/bureau/accounts/${bureauAccountId}/match`, { account_id: accountId, confirmed }),
  rematchBureau: () => post('/api/bureau/rematch'),
  portfolio: () => get('/api/portfolio'),

  /* ---- position: the one screen whose figures the USER asserts ---- */
  position: (includeArchived = false) =>
    get(`/api/position?include_archived=${includeArchived ? 'true' : 'false'}`),
  positionMappable: () => get('/api/position/mappable'),
  seedPosition: () => post('/api/position/seed'),
  addPositionItem: (fields) => post('/api/position/items', fields),
  updatePositionItem: (id, fields) => patch(`/api/position/items/${id}`, fields),
  /* Deliberately not a patch. Moving the review date is the act that says "I
     have looked at this and it is right", and it resets the roll-forward - so
     it must never happen as a side effect of fixing a typo. */
  reviewPositionItem: (id, reviewedOn) =>
    post(`/api/position/items/${id}/review`, { reviewed_on: reviewedOn }),
  deletePositionItem: (id, permanent = false) =>
    del(`/api/position/items/${id}?permanent=${permanent ? 'true' : 'false'}`),
  reviewPosition: (body) => post('/api/position/review', body),
  positionSnapshots: () => get('/api/position/snapshots'),
  positionSnapshot: (id) => get(`/api/position/snapshots/${id}`),
  deletePositionSnapshot: (id) => del(`/api/position/snapshots/${id}`),

  /* ---- agents ----
     A run is a JOB, not a request: several model round trips with tool
     execution between them takes tens of seconds, and an HTTP request held
     open that long dies to a proxy timeout, taking the analysis with it. */
  agents: () => get('/api/agents'),
  runAgent: (key, question = '') => post(`/api/agents/${key}/run`, { question }),
  agentRuns: (key, limit = 20) => get(`/api/agents/${key}/runs?limit=${limit}`),
  /* The transcript is every tool call and every result the agent read - what
     makes its figures checkable, and easily the largest thing in the record,
     so it is only fetched when somebody opens it. */
  agentRun: (id, { transcript = false } = {}) =>
    get(`/api/agents/runs/${id}?transcript=${transcript ? 'true' : 'false'}`),
  deleteAgentRun: (id) => del(`/api/agents/runs/${id}`),

  /* ---- explore ---- */
  querySchema: () => get('/api/query/schema'),
  /* `board` carries the dashboard's own range and filters; the server merges
     them into the widget's query, so one control re-cuts twelve widgets
     without any saved definition being rewritten. */
  runQuery: (q, board) => post('/api/query', { query: q, board }),
  boards: () => get('/api/dashboards'),
  board: (id) => get(`/api/dashboards/${id}`),
  boardTemplates: () => get('/api/dashboards/templates'),
  createBoard: (body) => post('/api/dashboards', body),
  updateBoard: (id, body) => put(`/api/dashboards/${id}`, body),
  deleteBoard: (id) => del(`/api/dashboards/${id}`),
  duplicateBoard: (id, name) => post(`/api/dashboards/${id}/duplicate`, { name }),
  runBoard: (id, board) => post(`/api/dashboards/${id}/run`, { board }),
  importBoard: (dashboard) => post('/api/dashboards/import', { dashboard }),
  createWidget: (boardId, widget) => post(`/api/dashboards/${boardId}/widgets`, widget),
  updateWidget: (boardId, widgetId, widget) =>
    put(`/api/dashboards/${boardId}/widgets/${widgetId}`, widget),
  deleteWidget: (boardId, widgetId) => del(`/api/dashboards/${boardId}/widgets/${widgetId}`),
  saveLayout: (boardId, layout) => put(`/api/dashboards/${boardId}/layout`, { layout }),
  exportQueryCsv: (q, board, filename = 'export') => download('/api/query/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: q, board, filename }),
  }, `${filename}.csv`),
  exportBoard: (id, name = 'dashboard') =>
    download(`/api/dashboards/${id}/export`, {}, `${name}.json`),

  /* ---- gmail ---- */
  gmailStatus: () => get('/api/gmail/status'),
  /* A full-page navigation, not a fetch: consent happens on Google's own page
     and the server has no browser of its own to open one in. */
  gmailConnect: (redirectTo = window.location.pathname + window.location.search) => {
    window.location.href =
      `/api/auth/google/start?purpose=gmail&redirect_to=${encodeURIComponent(redirectTo)}`;
  },
  gmailDisconnect: () => post('/api/gmail/disconnect'),
  gmailPeriods: () => get('/api/gmail/periods'),
  gmailIntents: () => get('/api/gmail/intents'),
  gmailIgnored: () => get('/api/gmail/ignored'),
  gmailSetIgnored: (senders) => put('/api/gmail/ignored', { excluded_senders: senders }),
  gmailScan: (maxMessages = 400, months = null, intent = 'statement') => post(
    `/api/gmail/scan?max_messages=${maxMessages}`
    + (months ? `&months=${months}` : '')
    + `&intent=${intent}`,
  ),
  gmailImportAlerts: (messageIds, scanJobId) =>
    post('/api/gmail/alerts/import', { message_ids: messageIds, scan_job_id: scanJobId }),
  /* `then_process` chains the parse on the SERVER when the download finishes.
     The chain used to be two awaits in the browser, so closing the tab between
     them left a pile of downloaded files nothing ever parsed. */
  gmailDownload: (attachments, { thenProcess = false, useLlm = false } = {}) =>
    post('/api/gmail/download', { attachments, then_process: thenProcess, use_llm: useLlm }),
  gmailProcess: (files) => post('/api/gmail/process', { files }),
  resumeJob: (id) => post(`/api/gmail/jobs/${id}/resume`),

  /* ---- upload ---- */
  upload: (files, { useLlm = true, horizonMonths = 6 } = {}) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f));
    form.append('use_llm', String(useLlm));
    form.append('horizon_months', String(horizonMonths));
    return request('/api/upload', { method: 'POST', body: form });
  },

  /* ---- staging: read, reviewed, and only then counted ----
     Everything a scan or an upload produces goes here first. No tab reads it,
     and `stagingProcess` is the only call in this file that changes a single
     figure anywhere in the app. */
  stagingReview: () => get('/api/staging/review'),
  stagingSelect: (body) => post('/api/staging/select', body),
  stagingSections: () => get('/api/staging/sections'),
  stagingForget: (intent) => post(
    intent ? `/api/staging/forget?intent=${encodeURIComponent(intent)}` : '/api/staging/forget'),
  stagingParse: (intent) => post(
    intent ? `/api/staging/parse?intent=${encodeURIComponent(intent)}` : '/api/staging/parse'),
  stagingProcess: () => post('/api/staging/process'),
  stagingRemove: (ids) => del('/api/staging/files', { ids, include: false }),
  stageScanResults: (body) => post('/api/staging/scan-results', body),

  /* ---- background jobs ----
     Kind-agnostic: a job is created by the file registry's retry as well as by
     the import wizard. `activeJobs` answers "is work happening right now?"
     without the caller having kept a job id, which is what lets progress
     survive closing the tab. */
  jobs: (params = {}) => get(`/api/jobs?${query(params)}`),
  activeJobs: () => api.jobs({ active: true }),
  job: (id) => get(`/api/jobs/${id}`),
  cancelJob: (id) => post(`/api/jobs/${id}/cancel`),

  /* ---- the operator's view ---- */
  adminOverview: (detail = true) => get(`/api/admin/overview?detail=${detail ? 'true' : 'false'}`),
};

/* Poll a job until it stops, calling back with each snapshot so a screen can
   render live progress. Tolerates a job interrupted by a server restart -
   resolving rather than hanging forever on a status that will never arrive. */
export async function watchJob(jobId, onTick, intervalMs = 700) {
  for (;;) {
    // eslint-disable-next-line no-await-in-loop
    const job = await api.job(jobId);
    onTick?.(job);
    if (!job.active) return job;
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => { setTimeout(r, intervalMs); });
  }
}

/* Flip the demo switch and start the app again from scratch.
 *
 * The reload is the point. Turning demo mode on or off changes which account
 * EVERY panel reads, and no single place owns all of that state. Re-reading
 * the session is not enough: doing only that left one screen reporting the
 * real ledger's months over the demo workspace's figures. A screen that mixes
 * the two ledgers is the one outcome this switch must never produce, and a
 * reload is the only way to rule it out rather than chase it panel by panel. */
export async function switchDemo(enabled) {
  await api.setDemo(enabled);
  window.location.reload();
}
