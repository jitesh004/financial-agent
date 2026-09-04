import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../lib';
import JobProgress from './JobProgress';
import { Callout, Card, Chip } from './ui';

/* The switch that decides whether this app spends money, and the run it gates.
 *
 * Off by default and stored on the server, not in this browser: the API is
 * what decides whether a model is called, so a preference the browser owned
 * would be a preference the server could not honour.
 *
 * The run is a job like an import - progress, a per-row trace, survives you
 * closing the tab - because it is the same shape of work. When it finishes it
 * reloads the ledger, so every tab reflects the new categories without a
 * manual refresh. */

export default function LlmSettings({ onLedgerChanged }) {
  const [settings, setSettings] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  const load = useCallback(
    () => api.settings().then(setSettings).catch((e) => setError(e.message)), []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => clearTimeout(timer.current), []);

  const toggle = async (next) => {
    setError(null);
    try {
      setSettings(await api.saveSettings({ use_llm: next }));
    } catch (e) { setError(e.message); }
  };

  /* Polls the job to the end, then refreshes both this panel's counts and the
     ledger every other tab reads from. */
  const watch = useCallback(async (jobId) => {
    for (;;) {
      const current = await api.job(jobId).catch(() => null);
      if (!current) break;
      setJob(current);
      if (!current.active) break;
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve) => { timer.current = setTimeout(resolve, 800); });
    }
    setBusy(false);
    await load();
    onLedgerChanged?.();
  }, [load, onLedgerChanged]);

  const run = async () => {
    setError(null);
    setBusy(true);
    try {
      const { job_id: id } = await api.runCategorize();
      await watch(id);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  if (!settings) return null;

  const pending = settings.uncategorized_count || 0;
  const done = job && !job.active && job.status === 'complete';

  return (
    <Card
      title="Model categorisation"
      sub={settings.llm_configured
        ? `${settings.llm_provider} configured`
        : 'no provider configured'}
    >
      <p style={{ color: 'var(--text-2)', fontSize: 13, margin: '0 0 12px' }}>
        Rules and the merchant cache categorise most rows without a model. What
        is left can go to a language model, which spends a metered budget —
        tokens on a paid provider, or a capped number of requests per day on a
        free one — so this is off until you turn it on, and imports never
        switch it on for you.
      </p>

      {error && <Callout tone="neg">{error}</Callout>}

      {!settings.llm_configured && (
        <Callout tone="warn">
          No API key is configured, so there is nothing to call. Add one to
          your <code>.env</code> and restart the API.
        </Callout>
      )}

      <label className="xp-check" style={{ margin: '10px 0 14px', fontSize: 13 }}>
        <input
          type="checkbox"
          checked={Boolean(settings.use_llm)}
          disabled={!settings.llm_configured}
          onChange={(e) => toggle(e.target.checked)}
        />
        Use a model for rows the rules cannot place
      </label>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <Chip tone={pending ? 'warn' : 'pos'}>
          {pending} uncategorised row{pending === 1 ? '' : 's'}
        </Chip>
        <button
          className="btn primary"
          disabled={!settings.use_llm || !pending || busy}
          onClick={run}
        >
          {busy ? 'Running…' : `Categorise ${pending} row${pending === 1 ? '' : 's'}`}
        </button>
        {!settings.use_llm && (
          <span className="xp-hint" style={{ textTransform: 'none' }}>
            Turn the switch on first.
          </span>
        )}
      </div>

      {job && (
        <div style={{ marginTop: 14 }}>
          <JobProgress job={job} title="Categorising" />
        </div>
      )}

      {done && job.result && (
        <Callout tone="pos" style={{ marginTop: 12 }}>
          <strong>{job.result.updated} of {job.result.considered} categorised.</strong>{' '}
          {job.result.changed_from_cache} came from the merchant cache and{' '}
          {job.result.changed_from_model} from the model.
          {job.result.still_uncategorized > 0 && (
            <> {job.result.still_uncategorized} could not be placed and stay in
              the review queue.</>
          )}
          {' '}Every tab has been refreshed.
        </Callout>
      )}
    </Card>
  );
}
