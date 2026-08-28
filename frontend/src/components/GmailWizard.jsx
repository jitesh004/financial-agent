import React, { useEffect, useMemo, useState } from 'react';
import { api, dateLabel, formatBytes, pollJob } from '../lib';
import AttachmentGroups from './AttachmentGroups';
import JobProgress from './JobProgress';
import { Callout, Card, Chip, Empty } from './ui';

/* Four stages, each reviewable before the next runs:

     connect -> scan -> select -> download -> process

   Nothing is downloaded until the user has seen the full list and chosen, and
   nothing is parsed until the files are actually on disk. Every stage reports
   real progress from the server rather than an animated guess. */

const CATEGORY_TONE = {
  bank: 'accent', card: 'pos', loan: 'warn', broker: '', unknown: '',
};

export default function GmailWizard({ onComplete }) {
  const [status, setStatus] = useState(null);
  const [stage, setStage] = useState('idle');   // idle|scanning|select|downloading|processing|done
  const [job, setJob] = useState(null);
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(() => new Set());
  const [error, setError] = useState(null);
  const [summary, setSummary] = useState(null);
  const [excluded, setExcluded] = useState([]);
  const [showExcluded, setShowExcluded] = useState(false);
  const [ignoredSenders, setIgnoredSenders] = useState([]);
  const [ignoredCount, setIgnoredCount] = useState(0);

  // Table controls
  const [search, setSearch] = useState('');
  const [excludedSenders, setExcludedSenders] = useState(() => new Set());
  const [excludedCategories, setExcludedCategories] = useState(() => new Set(['broker']));
  const [sort, setSort] = useState({ key: 'date_iso', dir: 'desc' });
  const [grouped, setGrouped] = useState(true);
  const [dateOrder, setDateOrder] = useState('desc');
  const [onlyMissingPassword, setOnlyMissingPassword] = useState(false);

  // How far back to search. 12 months is a sensible default: enough to see
  // a full year of seasonality without pulling a decade on the first run.
  const [periods, setPeriods] = useState([]);
  const [months, setMonths] = useState(12);
  const [maxMessages, setMaxMessages] = useState(() => suggestedCap(12));
  const [capTouched, setCapTouched] = useState(false);

  const refreshStatus = () => api.gmailStatus().then(setStatus).catch((e) => setError(e.message));
  useEffect(() => { refreshStatus(); }, []);
  useEffect(() => { api.gmailPeriods().then(setPeriods).catch(() => {}); }, []);
  useEffect(() => {
    api.gmailIgnored().then((r) => setIgnoredSenders(r.excluded_senders || [])).catch(() => {});
  }, []);

  // ---- Derived table data -------------------------------------------------

  const senders = useMemo(() => {
    const counts = new Map();
    for (const r of rows) {
      const key = r.sender_domain || r.sender_name;
      const prev = counts.get(key) || { key, name: r.sender_name, count: 0, category: r.category };
      prev.count += 1;
      counts.set(key, prev);
    }
    return [...counts.values()].sort((a, b) => b.count - a.count);
  }, [rows]);

  const categories = useMemo(() => {
    const counts = new Map();
    for (const r of rows) counts.set(r.category, (counts.get(r.category) || 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const out = rows.filter((r) => {
      if (excludedCategories.has(r.category)) return false;
      if (excludedSenders.has(r.sender_domain || r.sender_name)) return false;
      if (onlyMissingPassword && r.password_ready) return false;
      if (!needle) return true;
      return `${r.filename} ${r.sender_name} ${r.subject}`.toLowerCase().includes(needle);
    });

    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...out].sort((a, b) => {
      const av = a[sort.key] ?? '';
      const bv = b[sort.key] ?? '';
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [rows, search, excludedSenders, excludedCategories, onlyMissingPassword, sort]);

  // Selection is intersected with what's visible, so a hidden row can never be
  // silently downloaded - what you see selected is exactly what gets fetched.
  const effective = useMemo(
    () => visible.filter((r) => selected.has(rowKey(r))),
    [visible, selected],
  );

  const totalBytes = effective.reduce((s, r) => s + (r.size || 0), 0);
  const cachedCount = effective.filter((r) => r.cached).length;
  const missingPassword = effective.filter((r) => !r.password_ready).length;

  // ---- Actions ------------------------------------------------------------

  async function connect() {
    setError(null);
    try { await api.gmailConnect(); await refreshStatus(); }
    catch (e) { setError(e.message); }
  }

  async function runScan() {
    setError(null); setStage('scanning'); setJob(null);
    try {
      const { job_id: id } = await api.gmailScan(maxMessages, months);
      const done = await pollJob(id, setJob);
      const found = done.result?.attachments || [];
      setRows(found);
      setExcluded(done.result?.excluded || []);
      setIgnoredCount(done.result?.ignored_by_rule || 0);
      setIgnoredSenders(done.result?.excluded_senders || []);
      // Preselect everything except the categories excluded by default.
      setSelected(new Set(
        found.filter((r) => !excludedCategories.has(r.category)).map(rowKey),
      ));
      setStage('select');
    } catch (e) { setError(e.message); setStage('idle'); }
  }

  async function runDownloadAndProcess() {
    if (!effective.length) return;
    setError(null); setStage('downloading'); setJob(null);
    try {
      const { job_id: dlId } = await api.gmailDownload(effective);
      const dl = await pollJob(dlId, setJob);
      const files = dl.result?.files || [];

      setStage('processing'); setJob(null);
      const { job_id: prId } = await api.gmailProcess(files);
      const pr = await pollJob(prId, setJob);

      setSummary(pr.result);
      setStage('done');
    } catch (e) { setError(e.message); setStage('select'); }
  }

  async function cancel() {
    if (job?.id) { try { await api.gmailCancel(job.id); } catch { /* best effort */ } }
  }

  const toggleRow = (r) => setSelected((prev) => {
    const next = new Set(prev);
    const k = rowKey(r);
    next.has(k) ? next.delete(k) : next.add(k);
    return next;
  });

  const toggleMany = (items, select) => setSelected((prev) => {
    const next = new Set(prev);
    for (const r of items) {
      if (select) next.add(rowKey(r));
      else next.delete(rowKey(r));
    }
    return next;
  });

  const toggleAllVisible = () => {
    const allSelected = visible.every((r) => selected.has(rowKey(r)));
    setSelected((prev) => {
      const next = new Set(prev);
      for (const r of visible) {
        if (allSelected) next.delete(rowKey(r));
        else next.add(rowKey(r));
      }
      return next;
    });
  };

  async function ignoreSenderPermanently(domain) {
    const next = [...new Set([...ignoredSenders, domain])];
    try {
      await api.gmailSetIgnored(next);
      setIgnoredSenders(next);
      // Drop it from the current results too, so the effect is immediate
      // rather than only applying to the next scan.
      setRows((prev) => prev.filter((r) => !(r.sender_domain || '').includes(domain)));
      // (the sender panel still lists individual mailers, so ignoring is
      //  per-domain even though the table groups by institution)
      setIgnoredCount((n) => n + 1);
    } catch (e) { setError(e.message); }
  }

  async function unignoreAll() {
    try { await api.gmailSetIgnored([]); setIgnoredSenders([]); }
    catch (e) { setError(e.message); }
  }

  const toggleSender = (key) => setExcludedSenders((prev) => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  });

  const toggleCategory = (key) => setExcludedCategories((prev) => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  });

  const sortBy = (key) => setSort((s) => ({
    key, dir: s.key === key && s.dir === 'asc' ? 'desc' : 'asc',
  }));

  // ---- Render -------------------------------------------------------------

  if (!status) return <Card><div className="spinner" /></Card>;

  if (!status.available) return <SetupInstructions />;

  if (!status.connected) {
    return (
      <Card title="Import from Gmail">
        <Callout>
          Read-only access. Sign-in happens on Google’s own page — this app never
          sees your password, and the scope granted cannot send or delete mail.
        </Callout>
        <button className="btn primary" style={{ marginTop: 12 }} onClick={connect}>
          Connect Gmail
        </button>
        {error && <Callout tone="neg" style={{ marginTop: 10 }}>{error}</Callout>}
      </Card>
    );
  }

  return (
    <Card
      title="Import from Gmail"
      sub={status.cached_files > 0 ? `${status.cached_files} files cached locally` : 'Connected'}
    >
      {error && <Callout tone="neg">{error}</Callout>}

      {!status.profile_ready && (
        <Callout tone="warn">
          Your profile has no name/date-of-birth/PAN yet, so password-protected
          statements won’t open. Add them in <strong>Profile</strong> first.
        </Callout>
      )}

      {/* ---- Stage: idle ---- */}
      {stage === 'idle' && (
        <div style={{ marginTop: 12 }}>
          <p style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 0 }}>
            Scans your mailbox for statement PDFs and shows you everything it
            finds. Nothing is downloaded until you choose.
          </p>

          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end', margin: '14px 0' }}>
            <label style={{ display: 'block' }}>
              <div style={{ fontSize: 12.5, fontWeight: 550, marginBottom: 5 }}>
                Look back
              </div>
              <select value={months ?? ''} onChange={(e) => {
                const next = e.target.value ? Number(e.target.value) : null;
                setMonths(next);
                // The window is read newest-first, so a 10-year window with a
                // 400-email cap only ever reaches back about a year. Scale the
                // cap with the window unless the user has set one deliberately.
                if (!capTouched) setMaxMessages(suggestedCap(next));
              }}>
                {periods.map((p) => (
                  <option key={p.label} value={p.months ?? ''}>{p.label}</option>
                ))}
              </select>
            </label>

            <label style={{ display: 'block' }}>
              <div style={{ fontSize: 12.5, fontWeight: 550, marginBottom: 5 }}>
                Max emails to read
              </div>
              <select value={maxMessages}
                onChange={(e) => { setMaxMessages(Number(e.target.value)); setCapTouched(true); }}>
                {[100, 250, 500, 1000, 2500, 5000].map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </label>

            <button className="btn primary" onClick={runScan}>Scan mailbox</button>
          </div>

          <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
            A wider window finds more history but takes longer to read — roughly
            a second per 15 emails. Raise the email limit too: a 10-year window
            capped at 500 emails only reaches back about a year. Files already
            downloaded are re-used from the local cache either way.
          </div>
        </div>
      )}

      {/* ---- Stage: scanning ---- */}
      {stage === 'scanning' && (
        <div style={{ marginTop: 14 }}>
          <JobProgress job={job} title="Scanning your mailbox" onCancel={cancel} />
        </div>
      )}

      {/* ---- Stage: select ---- */}
      {stage === 'select' && (
        <>
          <div style={{ margin: '14px 0 10px' }}>
            <FilterBar
              ignoredSenders={ignoredSenders}
              ignoredCount={ignoredCount}
              onIgnoreSender={ignoreSenderPermanently}
              onUnignoreAll={unignoreAll}
              categories={categories}
              excludedCategories={excludedCategories}
              onToggleCategory={toggleCategory}
              senders={senders}
              excludedSenders={excludedSenders}
              onToggleSender={toggleSender}
              search={search}
              onSearch={setSearch}
              onlyMissingPassword={onlyMissingPassword}
              onToggleMissing={() => setOnlyMissingPassword((v) => !v)}
            />
          </div>

          <ExcludedPanel
            excluded={excluded}
            open={showExcluded}
            onToggle={() => setShowExcluded((v) => !v)}
          />

          <SelectionSummary
            visible={visible.length}
            total={rows.length}
            selected={effective.length}
            bytes={totalBytes}
            cached={cachedCount}
            missingPassword={missingPassword}
          />

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <button className="btn" style={{ padding: '3px 10px', fontSize: 12 }}
              onClick={() => setGrouped((v) => !v)}>
              {grouped ? 'Flat list' : 'Group by institution'}
            </button>
            <button className="btn" style={{ padding: '3px 10px', fontSize: 12 }}
              onClick={toggleAllVisible}>
              Select / deselect all shown
            </button>
          </div>

          {grouped ? (
            <AttachmentGroups
              rows={visible}
              selected={selected}
              onToggle={toggleRow}
              onToggleMany={toggleMany}
              dateOrder={dateOrder}
              onToggleDateOrder={() => setDateOrder((o) => (o === 'asc' ? 'desc' : 'asc'))}
            />
          ) : (
            <AttachmentTable
              rows={visible}
              selected={selected}
              onToggle={toggleRow}
              onToggleAll={toggleAllVisible}
              sort={sort}
              onSort={sortBy}
            />
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
            <button className="btn primary" disabled={!effective.length}
              onClick={runDownloadAndProcess}>
              Download &amp; process {effective.length} file{effective.length === 1 ? '' : 's'}
            </button>
            <button className="btn" onClick={runScan}>
              Re-scan ({periods.find((p) => (p.months ?? null) === months)?.label || 'window'})
            </button>
            <button className="btn" onClick={() => setStage('idle')}>Back</button>
          </div>
        </>
      )}

      {/* ---- Stage: downloading / processing ---- */}
      {(stage === 'downloading' || stage === 'processing') && (
        <div style={{ marginTop: 14 }}>
          <StageStrip active={stage} />
          <JobProgress
            job={job}
            title={stage === 'downloading' ? 'Downloading statements' : 'Parsing statements'}
            onCancel={cancel}
          />
        </div>
      )}

      {/* ---- Stage: done ---- */}
      {stage === 'done' && summary && (
        <div style={{ marginTop: 14 }}>
          <Callout tone="pos">
            <strong>Import complete.</strong>{' '}
            {summary.transaction_count?.toLocaleString('en-IN')} transactions across{' '}
            {summary.account_count} accounts.
          </Callout>
          <ResultTable statements={summary.statements || []} />
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button className="btn primary" onClick={() => onComplete?.()}>
              View dashboard
            </button>
            <button className="btn" onClick={() => { setStage('select'); setSummary(null); }}>
              Import more
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}

const rowKey = (r) => `${r.message_id}:${r.filename}:${r.size}`;

/* Emails to read for a given look-back window.

   Statement mail runs to a few hundred a year across a dozen institutions, and
   the scan reads newest-first - so the cap, not the date filter, is what really
   decides how far back a scan reaches. Defaulting these together stops the
   most confusing failure mode: choosing "10 years" and still seeing one. */
function suggestedCap(months) {
  if (months === null) return 5000;   // whole mailbox
  if (months <= 3) return 250;
  if (months <= 12) return 500;
  if (months <= 36) return 1000;
  if (months <= 60) return 2500;
  return 5000;
}

/* ---------- Sub-components ---------- */

function StageStrip({ active }) {
  const stages = [['downloading', 'Download'], ['processing', 'Parse']];
  return (
    <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
      {stages.map(([key, label]) => (
        <Chip key={key} tone={active === key ? 'accent' : ''}>{label}</Chip>
      ))}
    </div>
  );
}

function FilterBar({
  ignoredSenders, ignoredCount, onIgnoreSender, onUnignoreAll,
  categories, excludedCategories, onToggleCategory,
  senders, excludedSenders, onToggleSender,
  search, onSearch, onlyMissingPassword, onToggleMissing,
}) {
  const [showSenders, setShowSenders] = useState(false);
  const excludedCount = excludedSenders.size;

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <input type="search" placeholder="Search filename, sender or subject…"
          value={search} onChange={(e) => onSearch(e.target.value)}
          style={{ flex: 1, minWidth: 200 }} />
        <button className="btn" onClick={() => setShowSenders((v) => !v)}>
          Senders {excludedCount > 0 && `(${excludedCount} hidden)`}
        </button>
        <label className="chip" style={{ cursor: 'pointer' }}>
          <input type="checkbox" checked={onlyMissingPassword} onChange={onToggleMissing}
            style={{ marginRight: 4 }} />
          Only missing password
        </label>
      </div>

      {ignoredSenders?.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
            Permanently ignored{ignoredCount ? ` (${ignoredCount} skipped this scan)` : ''}:
          </span>
          {ignoredSenders.map((f) => <Chip key={f} tone="warn">{f}</Chip>)}
          <button className="btn" style={{ padding: '2px 9px', fontSize: 11.5 }}
            onClick={onUnignoreAll}>
            Clear
          </button>
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 12, color: 'var(--text-3)' }}>Type:</span>
        {categories.map(([cat, n]) => {
          const off = excludedCategories.has(cat);
          return (
            <button key={cat} onClick={() => onToggleCategory(cat)}
              className={`chip ${off ? '' : CATEGORY_TONE[cat] || 'accent'}`}
              style={{ cursor: 'pointer', opacity: off ? 0.45 : 1, border: 0 }}
              title={off ? `Click to include ${cat}` : `Click to exclude ${cat}`}>
              {off ? '✕ ' : '✓ '}{cat} ({n})
            </button>
          );
        })}
      </div>

      {showSenders && (
        <div style={{
          border: '1px solid var(--border)', borderRadius: 8, padding: 10,
          background: 'var(--surface-2)', maxHeight: 190, overflowY: 'auto',
        }}>
          <div style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: 8 }}>
            Untick to hide for this scan. <strong>Ignore</strong> removes the
            account permanently — use it for a family member's or a business
            account that shouldn't appear in your dashboard.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))', gap: 4 }}>
            {senders.map((s) => (
              <label key={s.key} style={{
                display: 'flex', gap: 7, alignItems: 'center',
                fontSize: 12.5, cursor: 'pointer', padding: '2px 0',
              }}>
                <input type="checkbox" checked={!excludedSenders.has(s.key)}
                  onChange={() => onToggleSender(s.key)} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.name}
                </span>
                <Chip>{s.count}</Chip>
                <button
                  className="btn"
                  style={{ padding: '1px 7px', fontSize: 11 }}
                  title={`Never import from ${s.key} again`}
                  onClick={(e) => { e.preventDefault(); onIgnoreSender(s.key); }}
                >
                  Ignore
                </button>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ExcludedPanel({ excluded, open, onToggle }) {
  /* What the filter rejected, and why.

     Shown rather than hidden: a filter you cannot inspect is one you have to
     second-guess, and the cost of a wrong exclusion (a silently missing month
     of history) is much higher than the cost of a wrong inclusion. */
  if (!excluded.length) return null;

  const byReason = excluded.reduce((acc, e) => {
    acc[e.reason] = (acc[e.reason] || 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 8,
      background: 'var(--surface-2)', padding: '9px 12px', marginBottom: 10,
    }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12.5, color: 'var(--text-2)' }}>
          <strong>{excluded.length}</strong> email
          {excluded.length === 1 ? '' : 's'} excluded
        </span>
        {Object.entries(byReason)
          .sort((a, b) => b[1] - a[1])
          .map(([reason, n]) => (
            <Chip key={reason} tone={reason === 'marketing' ? '' : 'warn'}>
              {n} {reason}
            </Chip>
          ))}
        <button className="btn" style={{ marginLeft: 'auto', padding: '3px 10px', fontSize: 12 }}
          onClick={onToggle}>
          {open ? 'Hide' : 'Review'}
        </button>
      </div>

      {open && (
        <div className="scroll-y" style={{
          maxHeight: 220, marginTop: 10,
          border: '1px solid var(--border)', borderRadius: 6,
          background: 'var(--surface)',
        }}>
          <div className="table-wrap" style={{ margin: 0, padding: 0 }}>
            <table style={{ tableLayout: 'fixed', width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: 84 }}>Date</th>
                  <th style={{ width: '28%' }}>Sender</th>
                  <th>Subject</th>
                  <th style={{ width: 130 }}>Excluded as</th>
                </tr>
              </thead>
              <tbody>
                {excluded.map((e, i) => (
                  <tr key={i}>
                    <td className="nowrap num" style={{ fontSize: 12 }}>
                      {e.date_iso ? dateLabel(e.date_iso) : '—'}
                    </td>
                    <td>
                      <div className="truncate" title={e.sender}>{e.sender_name}</div>
                    </td>
                    <td>
                      <div className="truncate" title={e.subject}>{e.subject}</div>
                    </td>
                    <td><Chip tone={e.reason === 'marketing' ? '' : 'warn'}>{e.reason}</Chip></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function SelectionSummary({ visible, total, selected, bytes, cached, missingPassword }) {
  return (
    <div style={{
      display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center',
      padding: '9px 12px', borderRadius: 8, background: 'var(--surface-2)',
      border: '1px solid var(--border)', fontSize: 12.5, marginBottom: 10,
    }}>
      <span><strong className="num">{selected}</strong> selected</span>
      <span style={{ color: 'var(--text-3)' }}>
        {visible} shown of {total} found
      </span>
      <span className="num" style={{ color: 'var(--text-3)' }}>{formatBytes(bytes)}</span>
      {cached > 0 && <Chip tone="pos">{cached} already cached</Chip>}
      {missingPassword > 0 && (
        <Chip tone="warn">{missingPassword} need profile details</Chip>
      )}
    </div>
  );
}

function AttachmentTable({ rows, selected, onToggle, onToggleAll, sort, onSort }) {
  if (!rows.length) {
    return <Empty title="Nothing matches these filters">Adjust the type or sender filters above.</Empty>;
  }
  const allChecked = rows.every((r) => selected.has(rowKey(r)));
  const arrow = (key) => (sort.key === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : '');

  return (
    <div className="table-wrap scroll-y" style={{ maxHeight: 460 }}>
      <table>
        <thead>
          <tr>
            <th style={{ width: 30 }}>
              <input type="checkbox" checked={allChecked} onChange={onToggleAll}
                title="Select all shown" />
            </th>
            <th onClick={() => onSort('filename')} style={{ cursor: 'pointer' }}>
              File{arrow('filename')}
            </th>
            <th onClick={() => onSort('sender_name')} style={{ cursor: 'pointer' }}>
              Sender{arrow('sender_name')}
            </th>
            <th>Subject</th>
            <th onClick={() => onSort('category')} style={{ cursor: 'pointer' }}>
              Type{arrow('category')}
            </th>
            <th>Password</th>
            <th className="right" onClick={() => onSort('size')} style={{ cursor: 'pointer' }}>
              Size{arrow('size')}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const checked = selected.has(rowKey(r));
            return (
              <tr key={rowKey(r)} style={{ opacity: checked ? 1 : 0.5 }}>
                <td>
                  <input type="checkbox" checked={checked} onChange={() => onToggle(r)} />
                </td>
                <td>
                  <div className="truncate" style={{ maxWidth: 210 }} title={r.filename}>
                    {r.filename}
                  </div>
                  {r.cached && <Chip tone="pos">cached</Chip>}
                </td>
                <td>
                  <div className="truncate" style={{ maxWidth: 140 }} title={r.sender}>
                    {r.sender_name}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-3)' }}>{r.sender_domain}</div>
                </td>
                <td>
                  <div className="truncate" style={{ maxWidth: 240 }} title={r.subject}>
                    {r.subject}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                    {r.date_iso ? dateLabel(r.date_iso) : dateLabel(r.date)}
                  </div>
                </td>
                <td><Chip tone={CATEGORY_TONE[r.category]}>{r.category}</Chip></td>
                <td>
                  <div title={r.password_explanation} style={{ cursor: 'help' }}>
                    <Chip tone={r.password_ready ? 'pos' : 'warn'}>
                      {r.password_rule}
                    </Chip>
                  </div>
                  {!r.password_ready && (
                    <div style={{ fontSize: 10.5, color: 'var(--warn)' }}>
                      missing profile detail
                    </div>
                  )}
                </td>
                <td className="right num nowrap">{formatBytes(r.size)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ResultTable({ statements }) {
  const tone = {
    ok: 'pos', unreconciled: 'warn', failed: 'neg',
    needs_password: 'warn', duplicate: '',
  };
  return (
    <div className="table-wrap scroll-y" style={{ maxHeight: 320, marginTop: 12 }}>
      <table>
        <thead>
          <tr>
            <th>File</th><th>Account</th>
            <th className="right">Rows</th><th>Status</th><th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {statements.map((s, i) => (
            <tr key={i}>
              <td><div className="truncate" style={{ maxWidth: 200 }}>{s.filename}</div></td>
              <td><div className="truncate" style={{ maxWidth: 160 }}>{s.account || '—'}</div></td>
              <td className="right num">{s.transaction_count ?? 0}</td>
              <td><Chip tone={tone[s.status]}>{s.status}</Chip></td>
              <td style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                <div className="truncate" style={{ maxWidth: 280 }} title={s.message}>
                  {s.message}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SetupInstructions() {
  return (
    <Card title="Import from Gmail" sub="Setup needed">
      <p style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 0 }}>
        Finds your bank and card statement emails, downloads the PDFs and analyzes
        them. Access is <strong>read-only</strong>, and sign-in happens on Google’s
        own page — this app never sees your Gmail password.
      </p>
      <Callout>
        <strong>What you need:</strong> a file called <code>credentials.json</code>.
        It isn’t your password — it’s a free ID card from Google that registers
        this app so Google will accept the sign-in.
      </Callout>
      <ol style={{ color: 'var(--text-2)', fontSize: 13, lineHeight: 1.75, paddingLeft: 20 }}>
        <li>Open <a href="https://console.cloud.google.com" target="_blank" rel="noreferrer"
          style={{ color: 'var(--accent)' }}>console.cloud.google.com</a> and create a project.</li>
        <li>Search <strong>Gmail API</strong> and click <strong>Enable</strong>.</li>
        <li>Open the <strong>OAuth consent screen</strong>, choose <strong>External</strong>,
          and add your own Gmail under <strong>Test users</strong>.</li>
        <li><strong>Credentials → Create Credentials → OAuth client ID</strong>,
          type <strong>Desktop app</strong>, then <strong>Download JSON</strong>.</li>
        <li>Save it as <code>credentials.json</code> in the project root and restart the API.</li>
      </ol>
      <Callout>
        Check it with{' '}
        <code>.venv/Scripts/python backend/tools/check_gmail_setup.py</code>
      </Callout>
    </Card>
  );
}
