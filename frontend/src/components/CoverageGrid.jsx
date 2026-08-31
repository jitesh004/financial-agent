import React, { useEffect, useMemo, useState } from 'react';
import { api, monthLabel, pollJob } from '../lib';
import { Callout, Card, Empty } from './ui';
import JobProgress from './JobProgress';

const STATUS_STYLE = {
  parsed: { background: 'var(--positive)', label: 'Parsed' },
  failed: { background: 'var(--warn)', label: 'Failed to parse' },
  missing: { background: 'var(--negative)', label: 'Not available' },
  na: { background: 'var(--surface-2)', label: '' },
};

/**
 * One row per account, one cell per calendar month: green (parsed), orange
 * (a file exists but failed to parse - click to retry), red (nothing was
 * ever found for this month - click to search Gmail for just it), or a dim
 * cell before the account's own history begins.
 */
export default function CoverageGrid({ onSelectFile }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [busyCell, setBusyCell] = useState(null); // "accountId:month" mid-fetch/retry
  const [notice, setNotice] = useState(null);
  const [bulkJob, setBulkJob] = useState(null);
  const [bulkRunning, setBulkRunning] = useState(false);

  function load() {
    api.coverage().then((res) => setRows(res.accounts)).catch((e) => setError(e.message));
  }
  useEffect(load, []);

  // Every account starts its OWN month list from its own first known
  // statement, so the columns need a shared, full-width axis to line up as a
  // real grid - built as the union of every row's months.
  const allMonths = useMemo(() => {
    if (!rows) return [];
    const set = new Set();
    rows.forEach((r) => r.months.forEach((m) => set.add(m.month)));
    return [...set].sort((a,b) => b.localeCompare(a));
  }, [rows]);

  const cellFor = (row, month) => row.months.find((m) => m.month === month);

  async function retryCell(fileId, key) {
    setBusyCell(key);
    setNotice(null);
    try {
      const res = await api.retryFile(fileId);
      setNotice({
        tone: res.status === 'ok' ? 'pos' : 'warn',
        text: res.message || `Retry finished: ${res.status}`,
      });
      load();
    } catch (e) {
      setNotice({ tone: 'neg', text: e.message });
    } finally {
      setBusyCell(null);
    }
  }

  async function fetchCell(accountId, month, key, label) {
    setBusyCell(key);
    setNotice(null);
    try {
      const { job_id: jobId } = await api.fetchMonth(accountId, month);
      const job = await pollJob(jobId);
      const result = job.result || {};
      setNotice({
        tone: result.status === 'ok' || result.status === 'unreconciled' ? 'pos' : 'warn',
        text: result.message || `${label} · ${monthLabel(month)}: ${job.message || 'done'}`,
      });
      load();
    } catch (e) {
      setNotice({ tone: 'neg', text: e.message });
    } finally {
      setBusyCell(null);
    }
  }

  async function fetchAllMissing() {
    setNotice(null);
    setBulkRunning(true);
    try {
      const { job_id: jobId } = await api.fetchAllMissing();
      await pollJob(jobId, setBulkJob);
      load();
    } catch (e) {
      setNotice({ tone: 'neg', text: e.message });
    } finally {
      setBulkRunning(false);
    }
  }

  if (error) return <Callout tone="neg">{error}</Callout>;
  if (!rows) {
    return (
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: 24 }}>
        <div className="spinner" /> Loading coverage…
      </div>
    );
  }
  if (!rows.length) {
    return <Empty title="No accounts yet">Import some statements to see coverage here.</Empty>;
  }

  const missingCount = rows.reduce(
    (s, r) => s + r.months.filter((m) => m.status === 'missing').length, 0);

  return (
    <>
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        Statement Coverage
        <button
          className="btn primary"
          style={{ marginLeft: 'auto', padding: '6px 14px', fontSize: 13 }}
          disabled={bulkRunning || !missingCount}
          onClick={fetchAllMissing}
        >
          {bulkRunning ? 'Fetching…' : `Fetch all missing statements from Gmail (${missingCount})`}
        </button>
      </div>

      {notice && <Callout tone={notice.tone} style={{ marginBottom: 12 }}>{notice.text}</Callout>}

      {bulkJob && (
        <Card style={{ marginBottom: 12 }}>
          <JobProgress job={bulkJob} title="Fetching missing statements" showTrace />
        </Card>
      )}

      <Card>
        <div style={{ display: 'flex', gap: 14, marginBottom: 12, fontSize: 12, color: 'var(--text-2)' }}>
          {Object.entries(STATUS_STYLE).filter(([k]) => k !== 'na').map(([key, s]) => (
            <span key={key} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 11, height: 11, borderRadius: 3, background: s.background, display: 'inline-block' }} />
              {s.label}
            </span>
          ))}
        </div>

        <div className="table-wrap">
          <div className="scroll-x" style={{ overflowX: "auto", paddingBottom: 8 }}><table style={{ borderSpacing: 3, borderCollapse: 'separate' }}>
            <thead>
              <tr>
                <th style={{ position: 'sticky', left: 0, background: 'var(--surface)', zIndex: 1 }}>
                  Account
                </th>
                {allMonths.map((m) => (
                  <th key={m} className="nowrap" style={{ fontSize: 10.5, textAlign: 'center', padding: '4px 2px' }}>
                    {monthLabel(m)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.account_id}>
                  <td className="nowrap" style={{
                    position: 'sticky', left: 0, background: 'var(--surface)',
                    fontSize: 12.5, fontWeight: 550, zIndex: 1,
                  }}>
                    {row.display_name}
                  </td>
                  {allMonths.map((month) => {
                    const cell = cellFor(row, month);
                    const status = cell ? cell.status : 'na';
                    const style = STATUS_STYLE[status];
                    const key = `${row.account_id}:${month}`;
                    const isBusy = busyCell === key;
                    const clickable = cell && (status === 'failed' || status === 'missing' || (status === 'parsed' && onSelectFile)) && !isBusy;

                    return (
                      <td key={month} style={{ padding: 2, textAlign: 'center' }}>
                        <button
                          disabled={!clickable}
                          title={cell
                            ? `${row.display_name} · ${monthLabel(month)} · ${style.label}`
                            : `${row.display_name} · ${monthLabel(month)} · before this account's history`}
                          onClick={() => {
                              if (!cell) return;
                              if (status === 'failed') retryCell(cell.file_id, key);
                              else if (status === 'missing') fetchCell(row.account_id, month, key, row.display_name);
                              else if (status === 'parsed' && onSelectFile) onSelectFile(cell.file_id);
                            }}
                          style={{
                            width: 22, height: 22, borderRadius: 5, border: 'none',
                            background: style.background,
                            opacity: status === 'na' ? 0.35 : 1,
                            cursor: clickable ? 'pointer' : 'default',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            padding: 0,
                          }}
                        >
                          {isBusy && <span className="spinner" style={{ width: 11, height: 11, borderWidth: 2 }} />}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>
      </Card>

      <Callout style={{ marginTop: 12 }}>
        Click an orange box to retry parsing that file. Click a red box to search
        your mailbox for just that account and month. Nothing already green is
        ever touched.
      </Callout>
    </>
  );
}
