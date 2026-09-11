/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 - Financial Statement & Document Import Wizard
   Comprehensive 6-stage import pipeline:
   1. Source: Connect Gmail and/or drop local statement PDFs/CSVs
   2. Scan: Fast mailbox header query across financial institutions
   3. Choose: Filter & select discovered statements or transactional alerts
   4. Read: Parse encrypted/complex statements into staging sandbox
   5. Review: Group-level reconciliation gates & balance verification
   6. Build: Deterministic re-compilation into active ledger
   ──────────────────────────────────────────────────────────────────────── */

import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../../core/api';
import { bytes } from '../../core/format';
import {
  Badge, Button, Callout, Chip, ConfirmButton, Icon, Loading, Modal,
} from '../../ui';
import JobProgress from '../../ui/JobProgress';
import { AiStep, ChooseStep, ReadStep, ScanStep, SourceStep } from './steps';
import { BuildStep, ReviewStep } from './review';
import { rowKey } from './useImport';

const STEPS = [
  { key: 'source', label: '1. Source' },
  { key: 'scanning', label: '2. Scan' },
  { key: 'choose', label: '3. Choose' },
  { key: 'parse', label: '4. Read' },
  /* Between Read and Review on purpose. Reading is when a model is
     consulted about a document's identity or its column layout, and Review
     is where those answers start shaping what the user approves - so the
     one place to see what was asked and what was believed belongs between
     them, before any of it is accepted. Categorisation happens later, in
     Build, and lands in the same list. */
  { key: 'ai', label: '5. AI' },
  { key: 'review', label: '6. Review' },
  { key: 'process', label: '7. Build' },
];

const STEP_FOR_STAGE = {
  scanning: 1,
  select: 2,
  downloaded: 2,
  downloading: 3,
  parsing: 3,
  interrupted: 3,
  // Parsing has finished, so the inferences it made are now worth reading:
  // the AI step is where a completed Read lands.
  staged: 4,
  processing: 6,
  done: 6,
};

export default function ImportWizard({ mailbox, open, onClose, onImported }) {
  const {
    status, periods, intents, error, stage, job, busy,
    rows, selection, setSelection, chosenIntents, toggleIntent, scanIntent,
    importableAlerts, sections, sourceResults, mailboxReady, mailboxAvailable,
  } = mailbox;

  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(0);
  const [staged, setStaged] = useState({});

  const stageStep = STEP_FOR_STAGE[stage] ?? 0;
  useEffect(() => {
    setReached((r) => Math.max(r, stageStep));
  }, [stageStep]);

  const reviewingAlerts = scanIntent === 'transactional';
  const scanId = mailbox.scanJob?.id;

  useEffect(() => {
    if (reviewingAlerts) {
      if (!importableAlerts.length) return;
      setSelection((prev) => (prev.size ? prev : new Set(importableAlerts.map((a) => a.message_id))));
      return;
    }
    if (!rows.length) return;
    setSelection((prev) => (prev.size ? prev : new Set(rows.map(rowKey))));
  }, [scanId, rows.length, reviewingAlerts, importableAlerts.length, setSelection]);

  const effective = useMemo(
    () => rows.filter((r) => selection.has(rowKey(r))),
    [rows, selection]
  );

  const toggleRow = (r) => {
    const next = new Set(selection);
    const k = rowKey(r);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setSelection(next);
  };

  const toggleMany = (items, select) => {
    const next = new Set(selection);
    for (const r of items) {
      if (select) next.add(rowKey(r));
      else next.delete(rowKey(r));
    }
    setSelection(next);
  };

  if (!open) return null;

  const view = STEPS[step].key;
  const behind = busy && step !== stageStep;

  const chosenCounts = rows.reduce((acc, r) => {
    if (!selection.has(rowKey(r))) return acc;
    const k = r.intent || 'statement';
    acc[k] = (acc[k] || 0) + 1;
    return acc;
  }, {});

  const stagedTotals = staged?.selected != null ? staged : {
    selected: (sections || []).reduce((n, x) => n + (x.selected || 0), 0),
    rows: (sections || []).reduce((n, x) => n + (x.rows || 0), 0),
    pending: (sections || []).reduce((n, x) => n + (x.pending || 0), 0),
    processed: staged?.processed,
  };

  const subtitle = status?.connected
    ? (status.cached_files > 0
      ? `Mailbox connected · ${status.cached_files} statements cached locally` : 'Mailbox connected')
    : 'Local workspace import';

  return (
    <Modal
      open={open}
      size="lg"
      title="Financial Statement Import Pipeline"
      sub={subtitle}
      onClose={onClose}
      tools={busy ? <Badge tone="acc">Job Running</Badge> : null}
      footer={(
        <div style={{ display: 'flex', alignItems: 'center', width: '100%', gap: 10 }}>
          {view === 'choose' && (
            <Button
              variant="primary"
              disabled={!effective.length}
              icon="download"
              onClick={async () => {
                await mailbox.startImport(effective);
                setStep(STEPS.findIndex((s) => s.key === 'parse'));
              }}
            >
              Download &amp; Read {effective.length} document{effective.length === 1 ? '' : 's'}
              {effective.length > 0 && (
                <span style={{ opacity: 0.75, marginLeft: 6 }}>
                  ({bytes(effective.reduce((s, r) => s + (r.size || 0), 0))})
                </span>
              )}
            </Button>
          )}

          {view === 'process' && stage === 'done' && (
            <Button
              variant="primary"
              icon="refresh"
              onClick={() => {
                mailbox.reset();
                setStep(0);
              }}
            >
              Import More Statements
            </Button>
          )}

          <Button
            disabled={step === 0}
            icon="arrow-left"
            onClick={() => setStep((s) => s - 1)}
          >
            Back
          </Button>

          <Button
            disabled={step >= STEPS.length - 1}
            onClick={() => setStep((s) => s + 1)}
          >
            Next Step
          </Button>

          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            <ConfirmButton
              title="Reset the import pipeline without touching active ledger"
              question="Forget all staged documents? Your active ledger will remain untouched."
              confirmLabel="Forget All Staged"
              onConfirm={async () => {
                await mailbox.forgetAll();
                setStaged({});
                setStep(0);
              }}
            >
              Start Over
            </ConfirmButton>

            <Button onClick={onClose}>
              {busy ? 'Run in background & close' : 'Close'}
            </Button>
          </div>
        </div>
      )}
    >
      {/* Visual Step Tracker */}
      <div className="steps">
        {STEPS.map((s, i) => (
          <React.Fragment key={s.key}>
            {i > 0 && <span className="step-link" aria-hidden="true" />}
            <button
              type="button"
              className={`step ${i === step ? 'on' : ''} ${i < reached ? 'done' : ''}`}
              onClick={() => setStep(i)}
              aria-current={i === step ? 'step' : undefined}
            >
              <span className="step-dot">
                {i < reached ? '✓' : i + 1}
              </span>
              <span>{s.label}</span>
            </button>
          </React.Fragment>
        ))}

        {behind && (
          <Button size="sm" onClick={() => setStep(stageStep)} style={{ marginLeft: 8 }}>
            Jump to Active: {STEPS[stageStep].label}
          </Button>
        )}
      </div>

      {error && <Callout tone="neg">{error}</Callout>}

      {behind && (
        <Callout tone="acc" icon="info">
          You are currently viewing <strong>{STEPS[step].label}</strong> while the background runner is on{' '}
          <strong>{STEPS[stageStep].label}</strong>.
          {busy ? ' Processing continues seamlessly in the background.' : ' Idle.'}
        </Callout>
      )}

      {status?.connected && !status.profile_ready && (
        <Callout tone="warn" icon="warning">
          Your local identity profile does not have your full name, DOB, or PAN yet. Password-protected
          PDF statements will require unlocking. Set your profile parameters in Settings &gt; Profile.
        </Callout>
      )}

      {!status && <Loading message="Querying mailbox synchronization state…" />}

      {!mailboxReady && (view === 'scanning' || view === 'choose') && (
        <Callout tone="warn" icon="warning">
          {mailboxAvailable
            ? 'No mailbox is currently linked. Connect Google Gmail on Step 1, or drop statement files directly from this machine.'
            : 'Gmail OAuth is not configured on this server. Add local PDF/CSV statement files on Step 1 — they are verified through the exact same parsing and balance gates.'}
        </Callout>
      )}

      {/* Step 1: Source */}
      {view === 'source' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          {!mailboxReady && (
            mailboxAvailable ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <Callout tone="acc" icon="info">
                  Read-only statement access. Authentication occurs entirely on Google&apos;s verified consent screen.
                  This agent never stores account passwords and requests zero send/delete scopes.
                </Callout>
                <div>
                  <Button variant="primary" icon="mail" onClick={mailbox.connect}>
                    Connect Gmail Read-Only
                  </Button>
                </div>
              </div>
            ) : <SetupInstructions />
          )}

          <SourceStep
            intents={intents}
            periods={periods}
            chosen={chosenIntents}
            onToggle={toggleIntent}
            settingsFor={mailbox.settingsFor}
            onSetting={mailbox.setSourceSetting}
            sections={sections}
            onUploaded={() => {
              mailbox.refreshSections?.();
              mailbox.refresh?.();
            }}
            mailboxReady={mailboxReady}
          />
        </div>
      )}

      {/* Step 2: Scan */}
      {view === 'scanning' && (
        <ScanStep
          intents={intents}
          chosen={chosenIntents}
          sections={sections}
          sourceJobs={mailbox.sourceJobs}
          busy={busy}
          onScan={async (key) => {
            const id = await mailbox.scanSource(key);
            mailbox.refreshSections?.();
            return id;
          }}
          onForget={async (key) => {
            await mailbox.forgetSource(key);
            setStaged({});
          }}
        />
      )}

      {/* Step 3: Choose */}
      {view === 'choose' && (
        <ChooseStep
          intents={intents}
          chosen={chosenIntents}
          sections={sections}
          sourceResults={sourceResults}
          rows={rows}
          selected={selection}
          onToggle={toggleRow}
          onToggleMany={toggleMany}
          ignoredSenders={mailbox.ignoredSenders}
          ignoredCount={mailbox.ignoredCount}
          onIgnore={(who, remove = false) => {
            const target = String(who || '').trim();
            if (!target) return;
            const current = mailbox.ignoredSenders || [];
            mailbox.setIgnored(remove
              ? current.filter((one) => one !== target)
              : [...new Set([...current, target])]);
          }}
          onClearIgnored={() => mailbox.setIgnored([])}
        />
      )}

      {/* Step 4: Parse / Read */}
      {view === 'parse' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <ReadStep
            intents={intents}
            chosen={chosenIntents}
            chosenCounts={chosenCounts}
            sections={sections}
            busy={busy}
            job={job}
            onParse={mailbox.parseSource}
            onRefresh={mailbox.refreshSections}
          />

          {(stage === 'downloading' || stage === 'parsing') && (
            <div style={{ padding: 14, borderRadius: 'var(--r)', background: 'var(--surface-2)', border: '1px solid var(--line)' }}>
              <JobProgress
                job={job}
                title={stage === 'downloading' ? 'Fetching encrypted statement attachments' : 'Parsing financial records into staging'}
                onCancel={mailbox.cancel}
              />
              <Callout style={{ marginTop: 12 }}>
                Parsed records remain isolated in staging and will not modify your live portfolio or ledger until verified in Step 5 &amp; 6.
              </Callout>
            </div>
          )}

          {stage === 'interrupted' && (
            <Callout tone="warn" icon="warning">
              <strong>Parsing was interrupted by server restart.</strong>{' '}
              {job?.current} of {job?.total} documents processed.{' '}
              <Button size="sm" onClick={mailbox.resume} style={{ marginLeft: 8 }}>
                Resume Parser
              </Button>
            </Callout>
          )}
        </div>
      )}

      {/* Step 5: Review */}
      {view === 'ai' && (
        <AiStep jobId={mailbox?.job?.id || ''} onRefresh={mailbox?.refresh} />
      )}

      {view === 'review' && (
        <ReviewStep onChanged={setStaged} />
      )}

      {/* Step 6: Build */}
      {view === 'process' && (
        <BuildStep
          staged={stagedTotals}
          job={job}
          stage={stage}
          onRun={mailbox.process}
          onFinished={() => {
            onImported?.();
            onClose();
          }}
        />
      )}
    </Modal>
  );
}

function SetupInstructions() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Callout tone="warn" icon="warning">
        <strong>Mailbox synchronization is unconfigured on this deployment.</strong> Local statement uploads work seamlessly without OAuth.
        To enable automated Gmail scanning, register a Google OAuth application.
      </Callout>

      <div
        style={{
          padding: '14px 18px',
          borderRadius: 'var(--r)',
          background: 'var(--surface-2)',
          border: '1px solid var(--line)',
        }}
      >
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8, color: 'var(--text)' }}>
          Quick OAuth Configuration Guide
        </div>
        <p style={{ fontSize: 12.5, color: 'var(--text-3)', marginBottom: 12 }}>
          Google OAuth requires a read-only client ID to scan for statements without transmitting sensitive credentials.
        </p>
        <ol style={{ paddingLeft: 20, fontSize: 12.5, lineHeight: 1.8, color: 'var(--text-2)' }}>
          <li>
            Visit{' '}
            <a
              href="https://console.cloud.google.com"
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--accent-text)', textDecoration: 'underline' }}
            >
              console.cloud.google.com <Icon name="external" size={11} />
            </a>{' '}
            and create or select a project.
          </li>
          <li>Enable the <strong>Gmail API</strong> in the API Library.</li>
          <li>Configure the <strong>OAuth Consent Screen</strong> (External, add test emails).</li>
          <li>
            Create an <strong>OAuth 2.0 Client ID</strong> (Web Application) with authorized redirect URI:{' '}
            <code style={{ background: 'var(--surface-3)', padding: '2px 6px', borderRadius: 'var(--r-xs)' }}>
              {typeof window !== 'undefined' ? `${window.location.origin}/api/auth/google/callback` : '/api/auth/google/callback'}
            </code>.
          </li>
          <li>
            Add <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> to your backend environment.
          </li>
        </ol>
      </div>
    </div>
  );
}
