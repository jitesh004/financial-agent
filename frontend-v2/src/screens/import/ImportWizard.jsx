/* The import, as a dialog reachable from anywhere.
 *
 * Closing this does not stop anything. The work is server-side and the stage
 * is derived from it (see useImport), so closing and reopening lands back
 * exactly where the import had got to - including across a page reload or an
 * API restart.
 *
 * Two positions are tracked, deliberately kept apart: the STEP THE WORK has
 * reached, and the STEP YOU ARE LOOKING AT. Binding the view to the work made
 * every screen you had already passed unreachable the moment the next one
 * started, and with a section per source it was worse than that - scanning
 * four sources finishes four times, and each completion yanked the screen
 * somewhere else. The rail unlocks as steps are reached; where you look is
 * yours.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../../core/api';
import { bytes } from '../../core/format';
import {
  Button, Callout, Chip, ConfirmButton, Icon, Loading, Modal,
} from '../../ui';
import JobProgress from '../../ui/JobProgress';
import { ChooseStep, ReadStep, ScanStep, SourceStep } from './steps';
import { BuildStep, ReviewStep } from './review';
import { rowKey } from './useImport';

const STEPS = [
  { key: 'source', label: 'Source' },
  { key: 'scanning', label: 'Scan' },
  { key: 'choose', label: 'Choose' },
  { key: 'parse', label: 'Read' },
  { key: 'review', label: 'Review' },
  { key: 'process', label: 'Build' },
];

const STEP_FOR_STAGE = {
  scanning: 1,
  select: 2,
  downloaded: 2,
  downloading: 3,
  parsing: 3,
  interrupted: 3,
  staged: 4,
  processing: 5,
  done: 5,
};

export default function ImportWizard({ mailbox, open, onClose, onImported }) {
  const {
    status, periods, intents, error, stage, job, busy,
    rows, selection, setSelection, chosenIntents, toggleIntent, scanIntent,
    importableAlerts, sections, sourceResults, mailboxReady, mailboxAvailable,
  } = mailbox;

  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(0);
  /* What is in staging, kept here because two steps need it: Review sets it
     and Build reads it. */
  const [staged, setStaged] = useState({});

  const stageStep = STEP_FOR_STAGE[stage] ?? 0;
  useEffect(() => { setReached((r) => Math.max(r, stageStep)); }, [stageStep]);

  // An alert scan produces a different kind of list, so the choose step
  // branches on what the LAST SCAN was for - not on what the picker currently
  // shows, which may already have been changed.
  const reviewingAlerts = scanIntent === 'transactional';

  /* A fresh scan's results arrive with nothing ticked. Everything found is
     preselected - the scan may well have finished while this was closed, so
     this reacts to the rows appearing rather than to the scan resolving. */
  const scanId = mailbox.scanJob?.id;
  useEffect(() => {
    if (reviewingAlerts) {
      if (!importableAlerts.length) return;
      setSelection((prev) => (prev.size ? prev
        : new Set(importableAlerts.map((a) => a.message_id))));
      return;
    }
    if (!rows.length) return;
    setSelection((prev) => (prev.size ? prev : new Set(rows.map(rowKey))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanId, rows.length, reviewingAlerts, importableAlerts.length]);

  /* What the footer will actually fetch: everything ticked, anywhere. The
     sections ARE the filter, and what you tick is what you get. */
  const effective = useMemo(
    () => rows.filter((r) => selection.has(rowKey(r))), [rows, selection]);

  const toggleRow = (r) => {
    const next = new Set(selection);
    const k = rowKey(r);
    if (next.has(k)) next.delete(k); else next.add(k);
    setSelection(next);
  };

  const toggleMany = (items, select) => {
    const next = new Set(selection);
    for (const r of items) {
      if (select) next.add(rowKey(r)); else next.delete(rowKey(r));
    }
    setSelection(next);
  };

  if (!open) return null;

  const view = STEPS[step].key;
  // Only ever a hint - "there is work happening on another step" - never a
  // reason to move somebody.
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
      ? `Connected · ${status.cached_files} files cached locally` : 'Connected')
    : 'Not connected';

  return (
    <Modal
      size="lg"
      title="Import"
      sub={subtitle}
      onClose={onClose}
      tools={busy ? <Chip tone="acc">running</Chip> : null}
      footer={(
        <>
          {view === 'choose' && (
            <Button
              variant="primary"
              disabled={!effective.length}
              icon="download"
              onClick={async () => {
                await mailbox.startImport(effective);
                // Moving on an explicit press is different from the view
                // following the work: you asked for this, and the thing you
                // asked for is reported one step along.
                setStep(STEPS.findIndex((s) => s.key === 'parse'));
              }}
            >
              Download &amp; read {effective.length} file{effective.length === 1 ? '' : 's'}
              {effective.length > 0 && (
                <span style={{ opacity: 0.75 }}>
                  {' '}· {bytes(effective.reduce((s, r) => s + (r.size || 0), 0))}
                </span>
              )}
            </Button>
          )}
          {view === 'process' && stage === 'done' && (
            <Button variant="primary" onClick={() => { mailbox.reset(); setStep(0); }}>
              Import more
            </Button>
          )}
          <Button disabled={step === 0} icon="chevron-left"
            onClick={() => setStep((s) => s - 1)}>Back</Button>
          <Button disabled={step >= STEPS.length - 1} iconRight="chevron"
            onClick={() => setStep((s) => s + 1)}>Next</Button>
          <span className="spacer" />
          <ConfirmButton
            title="Empty the wizard and start again. Your ledger is untouched."
            question="Forget every staged document? Your ledger is untouched."
            confirmLabel="Forget everything"
            onConfirm={async () => { await mailbox.forgetAll(); setStaged({}); setStep(0); }}
          >
            Start over
          </ConfirmButton>
          <Button onClick={onClose}>{busy ? 'Close and keep running' : 'Close'}</Button>
        </>
      )}
    >
      {/* Always. These steps are the import, and only two of the six are about
          a mailbox at all - so hiding the lot when Gmail is not configured
          took the file uploader with them, on exactly the deployments where it
          is the only way in. Both of the messages below told the reader to
          "add files from your computer instead", and neither left any way to
          do it. */}
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
                <span className="step-dot">{i < reached ? '✓' : i + 1}</span>
                {s.label}
              </button>
            </React.Fragment>
          ))}
        {behind && (
          <Button size="xs" onClick={() => setStep(stageStep)}>
            Back to {STEPS[stageStep].label}
          </Button>
        )}
      </div>

      {error && <Callout tone="neg">{error}</Callout>}

      {behind && (
        <Callout tone="acc">
          You are looking at <strong>{STEPS[step].label}</strong>; the import is on{' '}
          <strong>{STEPS[stageStep].label}</strong>.
          {busy ? ' It is still running — nothing here interrupts it.' : ' Nothing is running.'}
        </Callout>
      )}

      {status?.connected && !status.profile_ready && (
        <Callout tone="warn">
          Your profile has no name, date of birth or PAN yet, so password-protected
          statements will not open. Add them under <strong>Your details</strong> first.
        </Callout>
      )}

      {!status && <Loading label="Checking your mailbox connection…" />}

      {/* The mailbox's own state, said where it is relevant: on Source, where
          the alternative to it is, and on the two steps that are only about a
          mailbox. Never instead of the wizard. */}
      {!mailboxReady && (view === 'scanning' || view === 'choose') && (
        <Callout tone="warn">
          {mailboxAvailable
            ? 'No mailbox is connected, so there is nothing to scan. Connect one on the '
              + 'Source step, or add files from your computer there instead.'
            : 'Mailbox import is not configured on this server, so there is nothing to '
              + 'scan. Add files from your computer on the Source step instead — they go '
              + 'through exactly the same review.'}
        </Callout>
      )}

      {view === 'source' && !mailboxReady && (
        mailboxAvailable ? (
          <>
            <Callout tone="acc">
              Read-only access. Sign-in happens on Google&apos;s own page — this app never
              sees your password, and the scope granted cannot send or delete mail.
            </Callout>
            <div>
              <Button variant="primary" icon="mail" onClick={mailbox.connect}>
                Connect Gmail
              </Button>
            </div>
            <Callout>
              Or skip it entirely and add files from your computer below — they go
              through exactly the same review.
            </Callout>
          </>
        ) : <SetupInstructions />
      )}

        {view === 'source' && (
          <SourceStep
            intents={intents}
            periods={periods}
            chosen={chosenIntents}
            onToggle={toggleIntent}
            settingsFor={mailbox.settingsFor}
            onSetting={mailbox.setSourceSetting}
            sections={sections}
            onUploaded={() => { mailbox.refreshSections?.(); mailbox.refresh?.(); }}
            mailboxReady={mailboxReady}
          />
        )}

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
            onForget={async (key) => { await mailbox.forgetSource(key); setStaged({}); }}
          />
        )}

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
            onIgnore={(who) => who && mailbox.setIgnored(
              [...new Set([...(mailbox.ignoredSenders || []), who.trim()])])}
            onClearIgnored={() => mailbox.setIgnored([])}
          />
        )}

        {view === 'parse' && (
          <>
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
              <div className="card sunken" style={{ padding: 12 }}>
                <JobProgress
                  job={job}
                  title={stage === 'downloading' ? 'Downloading documents' : 'Reading documents'}
                  onCancel={mailbox.cancel}
                />
                <Callout style={{ marginTop: 10 }}>
                  Nothing read here is in your ledger — it goes to Review first. You can
                  close this. The work keeps running on the server, and the Import
                  button in the bar shows how it is getting on.
                </Callout>
              </div>
            )}
            {stage === 'interrupted' && (
              <Callout tone="warn">
                <strong>That run stopped when the server restarted.</strong>{' '}
                {job.current} of {job.total} finished.{' '}
                <Button size="sm" onClick={mailbox.resume}>Resume it</Button>{' '}
                — anything already read is not read twice.
              </Callout>
            )}
          </>
        )}

        {view === 'review' && <ReviewStep onChanged={setStaged} />}

        {view === 'process' && (
          <BuildStep
            staged={stagedTotals}
            job={job}
            stage={stage}
            onRun={mailbox.process}
            onFinished={() => { onImported?.(); onClose(); }}
          />
        )}
    </Modal>
  );
}

function SetupInstructions() {
  return (
    <>
      <Callout tone="warn">
        <strong>Mailbox import is not configured on this server.</strong> Adding files
        from your computer works regardless — the dropzone is below, and they go
        through exactly the same review. To scan a mailbox as well, whoever runs this
        deployment needs to set up an OAuth client.
      </Callout>
      <div className="card sunken" style={{ padding: 14 }}>
        <div style={{ fontWeight: 620, marginBottom: 8 }}>What is needed</div>
        <p className="small muted" style={{ marginBottom: 10 }}>
          The same Google OAuth client the app signs in with, configured on the server.
          It is not anyone&apos;s password — it is a free ID card from Google that
          registers this app so Google will accept the sign-in.
        </p>
        <ol className="small muted" style={{ paddingLeft: 20, lineHeight: 1.8 }}>
          <li style={{ listStyle: 'decimal' }}>
            Open{' '}
            <a href="https://console.cloud.google.com" target="_blank" rel="noreferrer">
              console.cloud.google.com <Icon name="external" size={11} />
            </a>{' '}
            and create a project.
          </li>
          <li style={{ listStyle: 'decimal' }}>
            Search <strong>Gmail API</strong> and click <strong>Enable</strong>.
          </li>
          <li style={{ listStyle: 'decimal' }}>
            Open the <strong>OAuth consent screen</strong>, choose <strong>External</strong>,
            and add your own Gmail under <strong>Test users</strong>.
          </li>
          <li style={{ listStyle: 'decimal' }}>
            <strong>Credentials → Create Credentials → OAuth client ID</strong>, type{' '}
            <strong>Web application</strong>, and add{' '}
            <code>{`${window.location.origin}/api/auth/google/callback`}</code> under{' '}
            <strong>Authorised redirect URIs</strong>.
          </li>
          <li style={{ listStyle: 'decimal' }}>
            Set <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> in
            the server&apos;s <code>.env</code>, and restart the API.
          </li>
        </ol>
      </div>
    </>
  );
}

