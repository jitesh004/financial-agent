/* ────────────────────────────────────────────────────────────────────────────
   Language model provider, key, models and agent step budget.

   `.env` supplies the deployment default; anything saved here overrides it for
   this account and persists. Fields left blank inherit — clearing one hands
   that single setting back to the environment rather than blanking the model
   out. The API key is write-only: the server returns a masked hint only.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../../core/api';
import { invalidate, useQuery } from '../../core/store';
import { useToast } from '../../core/toast';
import {
  Badge, Button, Callout, Card, Chip, ConfirmButton, Field, Loading, Select, Switch,
} from '../../ui';

const BLANK = {
  provider: '', api_key: '', base_url: '', model_fast: '', model_strong: '',
  agent_profile: '', pricing_tier: '',
};

export default function LlmSettings() {
  const toast = useToast();
  const { data: conf, loading, error, refetch } = useQuery('llm-config', () => api.llmConfig());

  const [draft, setDraft] = useState(BLANK);
  const [replaceKey, setReplaceKey] = useState(false);
  /* The key ring, as the screen holds it. Secrets are never sent to the
     browser, so an entry loaded from the server carries only a masked hint
     and `key: ''` - which the server reads as "keep the one you have under
     this label". A row the user types into carries the new secret. */
  const [ring, setRing] = useState([]);
  const [newKey, setNewKey] = useState({ label: '', key: '' });
  const [ringDirty, setRingDirty] = useState(false);
  const [busy, setBusy] = useState(null);
  const [probe, setProbe] = useState(null);

  /* Seed the form from whatever is in force, so the fields show the live
     configuration rather than empty boxes the user has to re-type. */
  useEffect(() => {
    if (!conf) return;
    setDraft({
      provider: conf.provider || '',
      api_key: '',
      base_url: conf.base_url || '',
      model_fast: conf.model_fast || '',
      model_strong: conf.model_strong || '',
      agent_profile: conf.agent_profile || 'auto',
      pricing_tier: conf.pricing_tier || 'free',
    });
    setReplaceKey(!conf.has_api_key);
    setRing((conf.api_keys || []).map((k) => ({ ...k, key: '' })));
  }, [conf]);

  const spec = useMemo(
    () => (conf?.providers || []).find((p) => p.key === draft.provider) || null,
    [conf, draft.provider],
  );

  if (loading) return <Loading message="Reading model configuration…" />;
  if (error) return <Callout tone="neg">Could not read model settings: {error.message}</Callout>;
  if (!conf) return null;

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));

  /* Switching provider invalidates the key, base URL and model names that
     belonged to the previous one, so offer that provider's own defaults. */
  const pickProvider = (key) => {
    const next = (conf.providers || []).find((p) => p.key === key);
    setProbe(null);
    setReplaceKey(true);
    set({
      provider: key,
      api_key: '',
      base_url: key === conf.env_defaults.provider
        ? (conf.env_defaults.base_url || next?.default_base_url || '')
        : (next?.default_base_url || ''),
      model_fast: key === conf.env_defaults.provider
        ? (conf.env_defaults.model_fast || next?.suggested_fast?.[0] || '')
        : (next?.suggested_fast?.[0] || ''),
      model_strong: key === conf.env_defaults.provider
        ? (conf.env_defaults.model_strong || next?.suggested_strong?.[0] || '')
        : (next?.suggested_strong?.[0] || ''),
    });
  };

  const save = async () => {
    setBusy('save');
    setProbe(null);
    try {
      const body = {
        provider: draft.provider,
        base_url: draft.base_url,
        model_fast: draft.model_fast,
        model_strong: draft.model_strong,
        agent_profile: draft.agent_profile,
        pricing_tier: draft.pricing_tier,
      };
      /* Only send the key when the user actually typed one: an empty string
         would clear a working key they never meant to touch. */
      if (replaceKey && draft.api_key.trim()) body.api_key = draft.api_key.trim();
      /* Sent whenever the ring has been touched. Entries keep `key: ''`
         unless the user typed a replacement, and the server resolves those
         against what it already holds - so reordering or deleting one key
         never round-trips the others through the browser. */
      if (ringDirty) body.api_keys = ring.map((k) => ({ label: k.label, key: k.key || '' }));
      await api.saveLlmConfig(body);
      invalidate('llm-config', 'settings', 'agents');
      await refetch();
      setReplaceKey(false);
      setRingDirty(false);
      setNewKey({ label: '', key: '' });
      set({ api_key: '' });
      toast.ok('Model configuration saved', 'Every AI task in the app now uses it.');
    } catch (e) {
      toast.fail('Could not save model configuration', e.message);
    } finally {
      setBusy(null);
    }
  };

  const test = async () => {
    setBusy('test');
    try {
      const result = await api.testLlmConfig();
      setProbe(result);
      if (result.ok) toast.ok('Provider answered', result.detail);
      else toast.warn('Provider did not answer', result.detail);
    } catch (e) {
      setProbe({ ok: false, detail: e.message });
      toast.fail('Connection test failed', e.message);
    } finally {
      setBusy(null);
    }
  };

  const reset = async () => {
    setBusy('reset');
    setProbe(null);
    try {
      await api.resetLlmConfig();
      invalidate('llm-config', 'settings', 'agents');
      await refetch();
      toast.ok('Reverted to environment defaults');
    } catch (e) {
      toast.fail('Could not reset', e.message);
    } finally {
      setBusy(null);
    }
  };

  const dirty = conf.provider !== draft.provider
    || (conf.base_url || '') !== draft.base_url
    || (conf.model_fast || '') !== draft.model_fast
    || (conf.model_strong || '') !== draft.model_strong
    || (conf.agent_profile || 'auto') !== draft.agent_profile
    || (replaceKey && Boolean(draft.api_key.trim()))
    || ringDirty;

  const modelLabel = spec?.key === 'azure' ? 'deployment' : 'model';

  return (
    <Card
      title="Language Model Provider"
      subtitle="Which provider, key and models every AI task in the app uses — categorisation, the written narrative, and the Copilot agents."
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 'var(--space-4)' }}>
        <Badge tone={conf.llm_configured ? 'pos' : 'warn'} size="sm">
          {conf.llm_configured ? 'Reachable' : 'Not configured'}
        </Badge>
        <Badge tone={conf.customised ? 'brand' : ''} size="sm">
          {conf.customised ? 'Configured here' : 'Inherited from .env'}
        </Badge>
        {conf.has_api_key && (
          <Chip size="sm" title={conf.api_key_from_env ? 'Key comes from .env' : 'Key saved from this screen'}>
            key {conf.api_key_hint}
          </Chip>
        )}
      </div>

      {!conf.providers?.length && (
        <Callout tone="warn">No providers are compiled into this build.</Callout>
      )}

      {/* Provider */}
      <Field
        label="Provider"
        hint={spec?.blurb || 'Pick the service that will answer model calls.'}
      >
        <Select
          value={draft.provider}
          onChange={pickProvider}
          placeholder="No model — rules only"
          options={(conf.providers || []).map((p) => [p.key, p.label])}
        />
      </Field>

      {spec && (
        <>
          <Field
            label={spec.key_label || 'API key'}
            hint={replaceKey
              ? spec.key_hint
              : 'Stored and in use. Enter a new key only if you want to replace it.'}
          >
            {replaceKey ? (
              <input
                className="input"
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder={spec.key_hint || 'Paste the key'}
                value={draft.api_key}
                onChange={(e) => set({ api_key: e.target.value })}
              />
            ) : (
              <div className="flex items-center gap-2 flex-wrap">
                <Chip size="sm">{conf.api_key_hint || 'set'}</Chip>
                <Button size="sm" variant="ghost" onClick={() => setReplaceKey(true)}>
                  Replace key
                </Button>
              </div>
            )}
          </Field>

          {/* Several keys for one provider.

              A free tier does not run out of tokens, it runs out of
              REQUESTS - 500 a day on this one, against 336 documents to
              import - and no amount of waiting inside a request clears a
              daily ceiling. Another key is the only thing that raises it,
              and AI Studio issues one per project. */}
          <Field
            label="Additional API keys"
            hint={'Tried in order. A key that hits its rate limit is stood down for a '
                  + 'minute and the next one is used; a key the provider rejects is not '
                  + 'tried again until restart.'}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {ring.length === 0 && (
                <span className="tiny muted">
                  Only the single key above is in use.
                </span>
              )}

              {/* Saving this list REPLACES it, so a partial save removes
                  every key it did not mention - and a credential is the
                  one thing here that cannot be regenerated from your own
                  documents. The previous list is kept for exactly that. */}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="tiny muted" style={{ flex: 1 }}>
                  Saving replaces the whole list. If a key disappears, the
                  previous list can be put back.
                </span>
                <Button
                  size="xs"
                  variant="ghost"
                  busy={busy === 'restore'}
                  onClick={async () => {
                    setBusy('restore');
                    try {
                      const r = await api.restoreLlmKeys();
                      invalidate('llm-config');
                      await refetch();
                      toast.ok(`Restored ${r.restored} key${r.restored === 1 ? '' : 's'}`,
                        r.labels.join(', '));
                    } catch (e) {
                      toast.fail('Nothing to restore', e.message);
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  Restore previous list
                </Button>
              </div>

              {ring.map((k, i) => (
                <div key={`${k.label}-${i}`} className="flex items-center gap-2 flex-wrap">
                  <input
                    className="input"
                    style={{ width: 150 }}
                    value={k.label}
                    placeholder="Label"
                    onChange={(e) => {
                      const next = [...ring];
                      next[i] = { ...next[i], label: e.target.value };
                      setRing(next); setRingDirty(true);
                    }}
                  />
                  <input
                    className="input"
                    style={{ flex: 1, minWidth: 180 }}
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={k.key}
                    placeholder={k.hint ? `stored \u00b7 ${k.hint}` : 'Paste the key'}
                    onChange={(e) => {
                      const next = [...ring];
                      next[i] = { ...next[i], key: e.target.value };
                      setRing(next); setRingDirty(true);
                    }}
                  />
                  <Badge
                    size="sm"
                    tone={k.state === 'rejected' ? 'neg'
                      : k.state === 'resting' ? 'warn' : 'pos'}
                    title={k.state === 'rejected'
                      ? 'The provider refused this key. Check it and save again.'
                      : k.state === 'resting'
                        ? `Rate limited. Back in about ${k.resting_for}s.`
                        : 'Ready to use.'}
                  >
                    {k.state === 'resting' ? `resting ${k.resting_for}s` : k.state || 'new'}
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setRing(ring.filter((_, j) => j !== i)); setRingDirty(true);
                    }}
                  >
                    Remove
                  </Button>
                </div>
              ))}

              <div className="flex items-center gap-2 flex-wrap">
                <input
                  className="input"
                  style={{ width: 150 }}
                  value={newKey.label}
                  placeholder="Label (e.g. project 2)"
                  onChange={(e) => setNewKey({ ...newKey, label: e.target.value })}
                />
                <input
                  className="input"
                  style={{ flex: 1, minWidth: 180 }}
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={newKey.key}
                  placeholder="Paste another key"
                  onChange={(e) => setNewKey({ ...newKey, key: e.target.value })}
                />
                <Button
                  size="sm"
                  disabled={!newKey.key.trim()}
                  onClick={() => {
                    setRing([...ring, {
                      label: newKey.label.trim() || `Key ${ring.length + 1}`,
                      key: newKey.key.trim(),
                      state: 'new',
                    }]);
                    setNewKey({ label: '', key: '' });
                    setRingDirty(true);
                  }}
                >
                  Add key
                </Button>
              </div>
            </div>
          </Field>

          {/* Which price list applies to this account.

              The same model has two: the free tier is metered in requests
              per day and costs nothing, the paid tier costs the published
              rate. That is a fact about the ACCOUNT, not the model, so
              nothing can infer it - and guessing wrong makes every figure
              on the Model Usage screen wrong in the same direction. */}
          <Field
            label="Billing tier"
            hint={'Decides how Model Usage costs each call. Free tier is metered in '
                  + 'requests per day, not money; paid tier is charged at the '
                  + 'model’s published rate in US dollars.'}
          >
            <Select
              value={draft.pricing_tier || 'free'}
              onChange={(v) => set({ pricing_tier: v })}
              options={[['free', 'Free tier — no charge, metered in requests/day'],
                ['paid', 'Paid tier — charged at the published USD rate']]}
            />
          </Field>

          <Field
            label={spec.key === 'azure' ? 'Resource endpoint' : 'Base URL'}
            hint={spec.key === 'azure'
              ? 'e.g. https://my-resource.openai.azure.com'
              : `Leave as-is unless you are proxying the provider. Default: ${spec.default_base_url || 'none'}`}
          >
            <input
              className="input"
              type="url"
              spellCheck={false}
              placeholder={spec.default_base_url}
              value={draft.base_url}
              onChange={(e) => set({ base_url: e.target.value })}
            />
          </Field>

          <div className="grid-2">
            <ModelField
              label={`High-volume ${modelLabel}`}
              hint="Merchant categorisation and letterhead lookups. Many small calls — pick something cheap and fast."
              value={draft.model_fast}
              suggestions={spec.suggested_fast}
              onChange={(v) => set({ model_fast: v })}
            />
            <ModelField
              label={`Reasoning ${modelLabel}`}
              hint="The written narrative and every Copilot agent. One call, long context — pick the most capable one you have."
              value={draft.model_strong}
              suggestions={spec.suggested_strong}
              onChange={(v) => set({ model_strong: v })}
            />
          </div>
        </>
      )}

      {/* Agent step budget */}
      <Field
        label="Agent step budget"
        hint={(conf.agent_profiles || []).find((p) => p.key === draft.agent_profile)?.blurb
          || 'How many reasoning steps and tools one agent run may spend.'}
      >
        <Select
          value={draft.agent_profile}
          onChange={(v) => set({ agent_profile: v })}
          options={(conf.agent_profiles || []).map((p) => [p.key, p.label])}
        />
      </Field>

      {probe && (
        <Callout tone={probe.ok ? 'pos' : 'neg'}>{probe.detail}</Callout>
      )}

      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)',
        marginTop: 'var(--space-4)', paddingTop: 'var(--space-3)',
        borderTop: '1px solid var(--border-subtle)', alignItems: 'center',
      }}>
        <Button variant="primary" busy={busy === 'save'} disabled={!dirty} onClick={save}>
          Save configuration
        </Button>
        <Button busy={busy === 'test'} onClick={test} title="Makes one tiny real call">
          Test connection
        </Button>
        {conf.customised && (
          <ConfirmButton
            variant="danger"
            question="Discard these settings and use the .env defaults?"
            confirmLabel="Revert"
            disabled={busy === 'reset'}
            onConfirm={reset}
          >
            Revert to .env
          </ConfirmButton>
        )}
        {dirty && <span className="tiny warn">Unsaved changes</span>}
      </div>

      {conf.env_defaults?.provider && (
        <div className="tiny muted" style={{ marginTop: 'var(--space-3)' }}>
          Environment default: {conf.env_defaults.provider}
          {conf.env_defaults.model_strong ? ` · ${conf.env_defaults.model_strong}` : ''}
          {' · '}{conf.env_defaults.agent_profile} step budget
          {conf.env_defaults.has_api_key ? ' · key present' : ' · no key'}
        </div>
      )}
    </Card>
  );
}

/* A free-text model name with the provider's known-good names one click away:
   the field has to accept anything the provider will honour, so a fixed
   dropdown would lock the user out of models this build has never heard of. */
function ModelField({ label, hint, value, suggestions = [], onChange }) {
  return (
    <div>
      <Field label={label} hint={hint}>
        <input
          className="input"
          type="text"
          spellCheck={false}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={suggestions[0] || 'model name'}
        />
      </Field>
      {suggestions.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: -2 }}>
          {suggestions.map((name) => (
            <button
              key={name}
              type="button"
              className={`chip ${name === value ? 'chip-accent' : ''}`}
              style={{ cursor: 'pointer', border: 'none' }}
              title={`Use ${name}`}
              onClick={() => onChange(name)}
            >
              {name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
