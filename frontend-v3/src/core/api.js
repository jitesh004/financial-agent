/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 API Client
   Direct typed requests to FastAPI backend with same-origin session credentials.
   ──────────────────────────────────────────────────────────────────────── */

let onUnauthorized = null;
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

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
  } catch {
    throw new ApiError('The server could not be reached. Check connection.', 0, null);
  }

  if (response.status === 401 && !path.startsWith('/api/auth/')) onUnauthorized?.();

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    let body = null;
    try {
      body = await response.json();
      detail = body.detail?.message || body.detail || body.message || detail;
    } catch {}
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

  /* Auth & Sessions */
  session: () => get('/api/auth/session'),
  authConfig: () => get('/api/auth/config'),
  logout: () => post('/api/auth/logout'),
  logoutEverywhere: () => post('/api/auth/logout-all'),
  activeSessions: () => get('/api/auth/sessions'),
  deleteAccount: (confirmEmail) => post('/api/auth/delete-account', { confirm_email: confirmEmail }),
  signInUrl: (redirectTo = window.location.pathname) =>
    `/api/auth/google/start?redirect_to=${encodeURIComponent(redirectTo)}`,

  /* Onboarding */
  onboarding: () => get('/api/onboarding'),
  onboardingStep: (step) => post('/api/onboarding/step', { step }),
  onboardingComplete: () => post('/api/onboarding/complete'),
  onboardingReopen: () => post('/api/onboarding/reopen'),

  /* Ledger, Overview & Analysis */
  health: () => get('/api/health'),
  dashboard: () => get('/api/dashboard'),
  periods: () => get('/api/periods'),
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

  /* Transactions */
  PAGE_MAX: 1000,
  transactions: (params = {}) => get(`/api/transactions?${query(params)}`),
  reviewQueue: (params = {}) =>
    api.transactions({ needs_review: true, limit: api.PAGE_MAX, ...params }),
  updateTransaction: (id, fields) => patch(`/api/transactions/${id}`, fields),
  bulkUpdate: (txnIds, fields) => patch('/api/transactions/bulk', { txn_ids: txnIds, ...fields }),
  splitTransaction: (id, splits) => post(`/api/transactions/${id}/split`, { splits }),
  claimTransaction: (id, body) => post(`/api/transactions/${id}/claim`, body),
  recategorize: (id, category) => patch(`/api/transactions/${id}`, { category }),

  /* Claims & Recurring */
  claims: (status) => get(`/api/claims${status ? `?status=${status}` : ''}`),
  settleClaim: (id, body) => post(`/api/claims/${id}/settle`, body),
  recurring: () => get('/api/recurring'),
  updateSeries: (id, fields) => patch(`/api/recurring/${id}`, fields),
  deleteSeries: (id) => del(`/api/recurring/${id}`),

  /* Files & Coverage */
  files: () => get('/api/files'),
  fileTransactions: (id) => get(`/api/files/${id}/transactions`),
  retryFile: (id, password) => post(`/api/files/${id}/retry`, password ? { password } : {}),
  coverage: () => get('/api/coverage'),
  fetchMonth: (accountId, month) => post(`/api/coverage/${accountId}/${month}/fetch`),
  fetchAllMissing: () => post('/api/coverage/fetch-all-missing'),

  /* Data Management */
  inventory: () => get('/api/data/inventory'),
  previewData: (scope) => get(`/api/data/preview/${scope}`),
  clearData: (scope, confirm) => post(`/api/data/clear/${scope}`, confirm ? { confirm } : {}),
  restoreSnapshot: (name) => post('/api/data/restore', { name }),
  deleteSnapshot: (name) => del(`/api/data/snapshots/${name}`),
  reset: () => post('/api/reset'),

  /* Profile */
  profile: () => get('/api/profile'),
  saveProfile: (profile) => put('/api/profile', profile),

  /* Settings & Demo */
  settings: () => get('/api/settings'),
  saveSettings: (body) => put('/api/settings', body),

  /* Language model provider, key, models and agent step budget.
     The API key is write-only: reads return a masked hint, never the key. */
  llmConfig: () => get('/api/settings/llm'),
  saveLlmConfig: (body) => put('/api/settings/llm', body),
  restoreLlmKeys: () => post('/api/settings/llm/keys/restore'),
  llmUsage: (period = 'all', key = '') =>
    get(`/api/llm/usage?period=${encodeURIComponent(period)}&key=${encodeURIComponent(key)}`),
  llmCalls: ({ period = 'all', key = '', purpose = '', status = '', limit = 100, offset = 0 } = {}) =>
    get(`/api/llm/calls?period=${encodeURIComponent(period)}&key=${encodeURIComponent(key)}`
      + `&purpose=${encodeURIComponent(purpose)}&status=${encodeURIComponent(status)}`
      + `&limit=${limit}&offset=${offset}`),
  stagedInferences: (jobId) =>
    get(`/api/staging/inferences${jobId ? `?job_id=${jobId}` : ''}`),
  resetLlmConfig: () => del('/api/settings/llm'),
  testLlmConfig: () => post('/api/settings/llm/test'),
  runCategorize: () => post('/api/settings/categorize'),
  demo: () => get('/api/settings/demo'),
  setDemo: (enabled) => post('/api/settings/demo', { enabled }),
  rebuildDemo: () => post('/api/settings/demo/rebuild'),

  /* Rules */
  rules: () => get('/api/rules'),
  explainTransaction: (id) => get(`/api/rules/explain/${id}`),
  testRules: (example) => post('/api/rules/test', example),

  /* Wealth & Credit */
  bureau: () => get('/api/bureau'),
  bureauReconciliation: () => get('/api/bureau/reconciliation'),
  matchBureauAccount: (bureauAccountId, accountId, confirmed) =>
    post(`/api/bureau/accounts/${bureauAccountId}/match`, { account_id: accountId, confirmed }),
  rematchBureau: () => post('/api/bureau/rematch'),
  portfolio: () => get('/api/portfolio'),

  /* Position */
  position: (includeArchived = false) =>
    get(`/api/position?include_archived=${includeArchived ? 'true' : 'false'}`),
  positionMappable: () => get('/api/position/mappable'),
  seedPosition: () => post('/api/position/seed'),
  addPositionItem: (fields) => post('/api/position/items', fields),
  updatePositionItem: (id, fields) => patch(`/api/position/items/${id}`, fields),
  reviewPositionItem: (id, reviewedOn) =>
    post(`/api/position/items/${id}/review`, { reviewed_on: reviewedOn }),
  deletePositionItem: (id, permanent = false) =>
    del(`/api/position/items/${id}?permanent=${permanent ? 'true' : 'false'}`),
  reviewPosition: (body) => post('/api/position/review', body),
  positionSnapshots: () => get('/api/position/snapshots'),
  positionSnapshot: (id) => get(`/api/position/snapshots/${id}`),
  deletePositionSnapshot: (id) => del(`/api/position/snapshots/${id}`),

  /* AI Agents */
  agents: () => get('/api/agents'),
  runAgent: (key, question = '') => post(`/api/agents/${key}/run`, { question }),
  agentRuns: (key, limit = 20) => get(`/api/agents/${key}/runs?limit=${limit}`),
  agentRun: (id, { transcript = false } = {}) =>
    get(`/api/agents/runs/${id}?transcript=${transcript ? 'true' : 'false'}`),
  deleteAgentRun: (id) => del(`/api/agents/runs/${id}`),

  /* Explore & Custom Dashboards */
  querySchema: () => get('/api/query/schema'),
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

  /* Gmail Integration */
  gmailStatus: () => get('/api/gmail/status'),
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
  gmailDownload: (attachments, { thenProcess = false, useLlm = false } = {}) =>
    post('/api/gmail/download', { attachments, then_process: thenProcess, use_llm: useLlm }),
  gmailProcess: (files) => post('/api/gmail/process', { files }),
  resumeJob: (id) => post(`/api/gmail/jobs/${id}/resume`),

  /* Upload */
  upload: (files, { useLlm = true, horizonMonths = 6 } = {}) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f));
    form.append('use_llm', String(useLlm));
    form.append('horizon_months', String(horizonMonths));
    return request('/api/upload', { method: 'POST', body: form });
  },

  /* Staging */
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

  /* Background Jobs */
  jobs: (params = {}) => get(`/api/jobs?${query(params)}`),
  activeJobs: () => api.jobs({ active: true }),
  job: (id) => get(`/api/jobs/${id}`),
  cancelJob: (id) => post(`/api/jobs/${id}/cancel`),

  /* Admin */
  adminOverview: (detail = true) => get(`/api/admin/overview?detail=${detail ? 'true' : 'false'}`),
};

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

export async function switchDemo(enabled) {
  await api.setDemo(enabled);
  window.location.reload();
}
