import React, { useState } from 'react';
import { api, switchDemo } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { useAuth } from '../core/auth';
import { useToast } from '../core/toast';
import { usePrefs, PREFS } from '../core/prefs';
import { count, dateLabel, monthLabelLong, titleCase } from '../core/format';
import { Button, Callout, Card, GlassCard, Chip, ConfirmButton, Select, Stat, Switch, Table, Badge } from '../ui';
import JobProgress from '../ui/JobProgress';

export default function Settings() {
  return (
    <div className="settings-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Preferences & Environment</h1>
            <Badge tone="brand" size="sm">System Configuration</Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Configure language model classification, connected authentication credentials, custom category taxonomies, and display density.
          </p>
        </div>
      </div>

      <ModelSettings />
      <AccountSettings />
      <DemoSettings />
      <CategoriesManager />
      <DisplaySettings />
    </div>
  );
}

function ModelSettings() {
  const toast = useToast();
  const { data: settings, refetch } = useQuery('settings', () => api.settings());
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);

  if (!settings) return null;
  const pending = settings.uncategorized_count || 0;
  const done = job && !job.active && job.status === 'complete';

  const toggle = async (next) => {
    try {
      await api.saveSettings({ use_llm: next });
      refetch();
      toast.ok('Model inference preferences updated');
    } catch (e) {
      toast.fail('Update failed', e.message);
    }
  };

  const run = async () => {
    setBusy(true);
    try {
      const { job_id: id } = await api.runCategorize();
      for (;;) {
        const current = await api.job(id).catch(() => null);
        if (!current) break;
        setJob(current);
        if (!current.active) break;
        await new Promise((r) => { setTimeout(r, 800); });
      }
      invalidate('dashboard', 'analysis', 'txns', 'workflow', 'settings');
      refetch();
    } catch (e) {
      toast.fail('Inference run failed', e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Semantic AI Classification"
      subtitle={settings.llm_configured ? `Active Model Provider: ${settings.llm_provider}` : 'No model provider configured in environment'}
    >
      <p className="small muted" style={{ marginBottom: 'var(--space-3)' }}>
        Deterministic regex rules categorize over 95% of rows without calling external APIs.
        For ambiguous merchant narrations, a language model can propose high-confidence categories.
      </p>

      {!settings.llm_configured && (
        <Callout tone="warn" style={{ marginBottom: 'var(--space-3)' }}>
          No API key detected in environment. Configure <code>GEMINI_API_KEY</code> or <code>OPENAI_API_KEY</code> in your server <code>.env</code>.
        </Callout>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: 'var(--space-3) 0', borderTop: '1px solid var(--border-subtle)' }}>
        <div>
          <div className="font-semibold small">Enable Language Model Fallback for Uncategorized Rows</div>
          <div className="tiny muted">Metered inference only executes when deterministic rules do not fire.</div>
        </div>
        <Switch
          checked={Boolean(settings.use_llm)}
          disabled={!settings.llm_configured}
          onChange={toggle}
          label="Model toggle"
        />
      </div>

      <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center', marginTop: 'var(--space-3)' }}>
        <Badge tone={pending ? 'warn' : 'pos'} size="md">
          {pending} uncategorized row{pending === 1 ? '' : 's'}
        </Badge>
        <Button
          variant="primary"
          size="sm"
          busy={busy}
          disabled={!settings.use_llm || !pending}
          onClick={run}
        >
          Run AI Categorizer on {pending} Rows
        </Button>
      </div>

      {job && <div style={{ marginTop: 'var(--space-3)' }}><JobProgress job={job} title="Classifying" /></div>}

      {done && job.result && (
        <Callout tone="pos" style={{ marginTop: 'var(--space-3)' }}>
          <strong>{job.result.updated} of {job.result.considered} rows classified.</strong>
          {' '}{job.result.changed_from_cache} matched via learned cache and {job.result.changed_from_model} via model inference.
        </Callout>
      )}
    </Card>
  );
}

function AccountSettings() {
  const { session, logout } = useAuth();

  return (
    <Card title="Authentication & Session" subtitle="Active identity and access token">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div className="font-semibold">{session?.email || 'Local User'}</div>
          <div className="tiny muted">Connected via Google OAuth 2.0</div>
        </div>
        <Button variant="ghost" size="sm" onClick={logout}>
          Sign Out
        </Button>
      </div>
    </Card>
  );
}

function DemoSettings() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const handleReset = async () => {
    setBusy(true);
    try {
      await api.resetDemo();
      invalidate('dashboard', 'analysis', 'txns', 'workflow');
      toast.ok('Demo sandbox reset to pristine state');
    } catch (e) {
      toast.fail('Reset failed', e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Demo Account Sandbox" subtitle="Preloaded synthetic statements illustrating multi-account cashflows">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div className="font-semibold small">Reset Demo Data</div>
          <div className="tiny muted">Restores synthetic demo transactions and wipes any experimental edits.</div>
        </div>
        <ConfirmButton
          size="sm"
          variant="danger"
          question="Reset all demo accounts and statements to original state?"
          confirmLabel="Reset Sandbox"
          onConfirm={handleReset}
        >
          Reset Demo Data
        </ConfirmButton>
      </div>
    </Card>
  );
}

function CategoriesManager() {
  const toast = useToast();
  const { data: categories = [], refetch } = useQuery('custom-categories', () => api.customCategories());
  const [name, setName] = useState('');
  const [group, setGroup] = useState('Living');

  const handleAdd = async () => {
    if (!name.trim()) return;
    try {
      await api.addCustomCategory({ name: name.trim().toLowerCase(), group });
      setName('');
      refetch();
      toast.ok(`Created custom category: ${name}`);
    } catch (e) {
      toast.fail('Could not create category', e.message);
    }
  };

  return (
    <Card title="Custom Category Taxonomy" subtitle="Define bespoke classification categories stored permanently with your ledger">
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
        <input
          className="input"
          placeholder="New Category Name (e.g. Pet Care, Woodworking)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ flex: 1 }}
        />
        <Select
          value={group}
          onChange={setGroup}
          options={[
            ['Living', 'Living & Housing'],
            ['Discretionary', 'Discretionary'],
            ['Investments', 'Investments & Savings'],
            ['Obligations', 'Obligations & Debt'],
          ]}
        />
        <Button variant="primary" onClick={handleAdd}>
          Add Category
        </Button>
      </div>

      {categories.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {categories.map((c) => (
            <Chip key={c.name}>
              {titleCase(c.name)} <span className="tiny muted">({c.group})</span>
            </Chip>
          ))}
        </div>
      )}
    </Card>
  );
}

function DisplaySettings() {
  const [prefs, setPref] = usePrefs();

  return (
    <Card title="Display & Viewport Preferences" subtitle="Local device rendering adjustments">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div className="font-semibold small">UI Animation Effects</div>
            <div className="tiny muted">Smooth keyframe transitions and radiant chart glows.</div>
          </div>
          <Switch
            checked={Boolean(prefs.animate)}
            onChange={(v) => setPref('animate', v)}
            label="Animate"
          />
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div className="font-semibold small">High-Density Ledger Rows</div>
            <div className="tiny muted">Reduce table row padding to fit more transaction lines on screen.</div>
          </div>
          <Switch
            checked={prefs.density === 'compact'}
            onChange={(v) => setPref('density', v ? 'compact' : 'comfortable')}
            label="Density"
          />
        </div>
      </div>
    </Card>
  );
}
