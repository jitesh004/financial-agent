import React, { useEffect, useMemo, useState } from 'react';
import { api, dateLabel, formatBytes, money, titleCase } from '../lib';
import { Callout, Card, Chip, Empty } from './ui';
import CoverageGrid from './CoverageGrid';

const STATUS = {
  parsed: { tone: 'pos', label: 'Parsed' },
  unreconciled: { tone: 'warn', label: 'Parsed · did not balance' },
  failed: { tone: 'neg', label: 'Failed' },
  needs_password: { tone: 'warn', label: 'Password needed' },
  duplicate: { tone: '', label: 'Duplicate, skipped' },
  pending: { tone: '', label: 'Pending' },
};

const PASSWORD = {
  open: { tone: 'pos', label: 'Open' },
  not_encrypted: { tone: '', label: 'Not protected' },
  locked: { tone: 'neg', label: 'Locked' },
  unknown: { tone: '', label: 'Unknown' },
};

const SOURCE_LABEL = { gmail: 'Gmail', upload: 'Uploaded' };

/**
 * Every file the app has ever touched, whatever happened to it: which
 * password opened it, whether it parsed, and - for one that didn't - a way to
 * fix the password and try again without re-processing everything else.
 */
export default function FilesAndPasswords() {
  const [files, setFiles] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('');
  const [expanded, setExpanded] = useState(null); // file id whose rows are shown
  const [drill, setDrill] = useState(null);        // { file, transactions } | 'loading'
  const [retrying, setRetrying] = useState(null);  // file id currently retrying
  const [passwordDrafts, setPasswordDrafts] = useState({});
  const [notice, setNotice] = useState(null);

  function load() {
    api.files().then(setFiles).catch((e) => setError(e.message));
  }
  useEffect(load, []);

  const visible = useMemo(() => {
    if (!files) return [];
    if (filter === 'failed') {
      return files.filter((f) => ['failed', 'needs_password'].includes(f.parse_status));
    }
    if (filter === 'parsed') {
      return files.filter((f) => ['parsed', 'unreconciled'].includes(f.parse_status));
    }
    return files;
  }, [files, filter]);

  const counts = useMemo(() => {
    const c = { all: files?.length || 0, parsed: 0, failed: 0 };
    (files || []).forEach((f) => {
      if (['parsed', 'unreconciled'].includes(f.parse_status)) c.parsed += 1;
      if (['failed', 'needs_password'].includes(f.parse_status)) c.failed += 1;
    });
    return c;
  }, [files]);

  async function toggleExpand(file) {
    if (expanded === file.id) { setExpanded(null); return; }
    setExpanded(file.id);
    setDrill('loading');
    try {
      const res = await api.fileTransactions(file.id);
      setDrill(res);
    } catch (e) {
      setDrill(null);
      setError(e.message);
    }
  }

  async function retry(file) {
    setRetrying(file.id);
    setNotice(null);
    try {
      const res = await api.retryFile(file.id, passwordDrafts[file.id]);
      setNotice({
        tone: res.status === 'failed' || res.status === 'needs_password' ? 'warn' : 'pos',
        text: res.status === 'ok'
          ? `${file.filename}: parsed ${res.transaction_count} transaction(s) into ${res.account}.`
          : res.message || `${file.filename}: still ${res.status}.`,
      });
      load();
      if (expanded === file.id) toggleExpand(file);
    } catch (e) {
      setNotice({ tone: 'neg', text: `${file.filename}: ${e.message}` });
    } finally {
      setRetrying(null);
    }
  }

  
  async function handleSelectFile(fileId) {
    console.log("handleSelectFile called with:", fileId);
    setFilter('all');
    const file = files?.find(f => f.id === fileId);
    console.log("File found in list?", !!file);
    if (!file) return;
    
    if (expanded !== fileId) {
      console.log("Expanding...");
      setExpanded(fileId);
      setDrill('loading');
      try {
        const res = await api.fileTransactions(fileId);
        console.log("API response:", res);
        setDrill(res);
      } catch (e) {
        console.error("API error:", e);
        setDrill(null);
        setError(e.message);
      }
    } else {
      console.log("Already expanded!");
    }
    
    setTimeout(() => {
      const el = document.getElementById(`file-row-${fileId}`);
      console.log("Scroll target el:", el);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 500); // 500ms instead of 100ms just to be safe with React re-renders!
  }

if (error) return <Callout tone="neg">{error}</Callout>;
  if (!files) {
    return (
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: 24 }}>
        <div className="spinner" /> Loading files…
      </div>
    );
  }
  if (!files.length) {
    return <Empty title="No files yet">Upload statements, or connect Gmail, to see them here.</Empty>;
  }

  return (
    <>
      <CoverageGrid onSelectFile={handleSelectFile} />

      <div className="section-title" style={{ marginTop: 26 }}>Files & Passwords</div>

      {notice && (
        <Callout tone={notice.tone} style={{ marginBottom: 12 }}>{notice.text}</Callout>
      )}

      <Card style={{ marginBottom: 12 }}>
        <div className="seg">
          <button className={`seg-btn ${filter === '' ? 'active' : ''}`} onClick={() => setFilter('')}>
            All ({counts.all})
          </button>
          <button className={`seg-btn ${filter === 'parsed' ? 'active' : ''}`} onClick={() => setFilter('parsed')}>
            Parsed ({counts.parsed})
          </button>
          <button className={`seg-btn ${filter === 'failed' ? 'active' : ''}`} onClick={() => setFilter('failed')}>
            Needs attention ({counts.failed})
          </button>
        </div>
      </Card>

      <Card>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Source</th>
                <th className="right">Size</th>
                <th>Password</th>
                <th>Status</th>
                <th className="right">Rows</th>
                <th>Last attempt</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visible.map((f) => {
                const status = STATUS[f.parse_status] || { tone: '', label: f.parse_status };
                const pw = PASSWORD[f.password_status] || { tone: '', label: f.password_status };
                const canRetry = ['failed', 'needs_password', 'unreconciled'].includes(f.parse_status);
                const canDrill = f.parse_status === 'parsed' || f.parse_status === 'unreconciled';
                return (
                  <React.Fragment key={f.id}>
                    <tr id={`file-row-${f.id}`}>
                      <td>
                        <div className="truncate" style={{ maxWidth: 260 }} title={f.filename}>
                          {f.filename}
                        </div>
                        {f.institution_guess && (
                          <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                            {f.institution_guess}{f.account_type_guess ? ` · ${titleCase(f.account_type_guess)}` : ''}
                          </span>
                        )}
                      </td>
                      <td><Chip>{SOURCE_LABEL[f.source] || f.source}</Chip></td>
                      <td className="right num nowrap" style={{ fontSize: 12, color: 'var(--text-3)' }}>
                        {formatBytes(f.size_bytes)}
                      </td>
                      <td>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                          <Chip tone={pw.tone}>{pw.label}</Chip>
                          {f.password_redacted && (
                            <span style={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'monospace' }}>
                              {f.password_redacted}
                            </span>
                          )}
                        </div>
                      </td>
                      <td><Chip tone={status.tone}>{status.label}</Chip></td>
                      <td className="right num">{f.transaction_count || '—'}</td>
                      <td className="nowrap" style={{ fontSize: 12, color: 'var(--text-3)' }}>
                        {dateLabel(f.last_attempted_at)}
                      </td>
                      <td>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'flex-end' }}>
                          {canDrill && (
                            <button className="btn" style={{ padding: '4px 10px', fontSize: 12 }}
                              onClick={() => toggleExpand(f)}>
                              {expanded === f.id ? 'Hide' : 'View rows'}
                            </button>
                          )}
                          {canRetry && (
                            <button
                              className="btn primary"
                              style={{ padding: '4px 10px', fontSize: 12 }}
                              disabled={retrying === f.id}
                              onClick={() => retry(f)}
                            >
                              {retrying === f.id ? 'Retrying…' : 'Retry'}
                            </button>
                          )}
                        </div>
                        {f.parse_status === 'needs_password' && (
                          <input
                            type="text"
                            placeholder="Try a specific password…"
                            value={passwordDrafts[f.id] || ''}
                            onChange={(e) => setPasswordDrafts((d) => ({ ...d, [f.id]: e.target.value }))}
                            style={{ marginTop: 6, fontSize: 12, padding: '4px 8px', width: 180 }}
                          />
                        )}
                      </td>
                    </tr>
                    {expanded === f.id && (
                      <tr>
                        <td colSpan={8} style={{ background: 'var(--surface-2)', padding: 12 }}>
                          {drill === 'loading' ? (
                            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                              <div className="spinner" /> Loading rows…
                            </div>
                          ) : !drill?.transactions?.length ? (
                            <span style={{ fontSize: 13, color: 'var(--text-3)' }}>
                              No transactions recorded for this file.
                            </span>
                          ) : (
                            <div className="table-wrap scroll-y" style={{ maxHeight: 320 }}>
                              <table>
                                <thead>
                                  <tr>
                                    <th>Date</th><th>Description</th><th>Category</th>
                                    <th className="right">Amount</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {drill.transactions.map((t) => (
                                    <tr key={t.id}>
                                      <td className="nowrap">{dateLabel(t.date)}</td>
                                      <td style={{ wordBreak: 'break-word', whiteSpace: 'normal', lineHeight: '1.4' }}>
                                        {t.description}
                                      </td>
                                      <td><Chip>{titleCase(t.category)}</Chip></td>
                                      <td className="right num nowrap" style={{
                                        color: t.direction === 'credit' ? 'var(--positive)' : 'inherit',
                                      }}>
                                        {t.direction === 'credit' ? '+' : '−'}{money(t.amount, true)}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <Callout style={{ marginTop: 12 }}>
        A password that works is remembered against the file's own content, so
        the next load opens it directly - even under a different filename.
        Retrying re-parses only this one file; everything else stays untouched.
      </Callout>
    </>
  );
}
