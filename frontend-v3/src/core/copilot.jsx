/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Omni-Copilot Context Provider
   Global state and orchestrator for the AI Financial Assistant Drawer (⌘J).
   ──────────────────────────────────────────────────────────────────────── */

import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { api, watchJob } from './api';
import { useToast } from './toast';

const CopilotContext = createContext(null);

export const CONTEXT_PROMPTS = {
  overview: [
    'Audit my top 3 spending leaks and irregular charges',
    'Calculate my emergency fund runway if income drops',
    'Where is my cashflow bleeding the most this month?',
  ],
  debt: [
    'Compare Snowball vs Avalanche payoff strategies for my loans',
    'How much interest will I save if I prepay ₹25,000 monthly?',
    'What is my effective blended borrowing cost across cards and loans?',
  ],
  budget: [
    'Analyze my 50/30/20 compliance and discretionary headroom',
    'Where can I safely trim ₹15,000 without affecting necessities?',
    'Identify bill shocks projected for next month',
  ],
  spending: [
    'Show Pareto distribution of top 10 merchants by volume',
    'Which categories expanded fastest over the past 3 months?',
    'Flag unexpected or unclassified merchant charges',
  ],
  recurring: [
    'Audit all recurring subscriptions and identify price hikes',
    'Calculate total annualized run-rate of all software & media bills',
    'Which subscriptions had no usage or look forgotten?',
  ],
  forecast: [
    'Will my balance drop below ₹50,000 in the next 60 days?',
    'What is the worst-case cash pinch scenario in the 90-day cone?',
    'How will paying off my card bill impact my 30-day liquidity?',
  ],
  ledger: [
    'Find potential duplicate charges or double deductions',
    'Explain the categorization rule applied to high-value items',
    'List all UPI transactions exceeding ₹10,000',
  ],
  position: [
    'Assess my asset allocation balance between debt and equity',
    'Identify accounts whose balances have aged without review',
    'What is my true net worth growth rate annualized?',
  ],
  credit: [
    'Reconcile credit bureau accounts with active bank accounts',
    'What accounts are hurting my credit utilization ratio?',
    'Are there any unrecognised inquiries on my credit report?',
  ],
};

export function CopilotProvider({ children }) {
  const [open, setOpen] = useState(false);
  const [activeScreen, setActiveScreen] = useState('overview');
  const [activePrompt, setActivePrompt] = useState('');
  const [running, setRunning] = useState(false);
  const [activeJob, setActiveJob] = useState(null);
  const [currentRun, setCurrentRun] = useState(null);
  const [history, setHistory] = useState([]);
  const toast = useToast();

  const openCopilot = useCallback((screen = null, initialPrompt = null) => {
    if (screen) setActiveScreen(screen);
    if (initialPrompt) setActivePrompt(initialPrompt);
    setOpen(true);
  }, []);

  const closeCopilot = useCallback(() => setOpen(false), []);
  const toggleCopilot = useCallback(() => setOpen((v) => !v), []);

  const runAgentJob = useCallback(async (agentKey, userQuestion = '') => {
    setRunning(true);
    setCurrentRun(null);
    setActiveJob(null);
    try {
      const { job_id } = await api.runAgent(agentKey, userQuestion);
      const finished = await watchJob(job_id, (snap) => setActiveJob(snap));
      if (finished.status === 'failed') {
        throw new Error(finished.errors?.join('; ') || 'Agent execution failed');
      }
      const runId = finished.result?.run_id || finished.result?.id;
      if (runId) {
        const fullRun = await api.agentRun(runId, { transcript: true });
        setCurrentRun(fullRun);
        setHistory((prev) => [fullRun, ...prev.slice(0, 15)]);
      }
    } catch (err) {
      toast.fail('Copilot run failed', err.message);
    } finally {
      setRunning(false);
      setActiveJob(null);
    }
  }, [toast]);

  const value = useMemo(() => ({
    open,
    openCopilot,
    closeCopilot,
    toggleCopilot,
    activeScreen,
    setActiveScreen,
    activePrompt,
    setActivePrompt,
    running,
    activeJob,
    currentRun,
    history,
    runAgentJob,
    suggestedPrompts: CONTEXT_PROMPTS[activeScreen] || CONTEXT_PROMPTS.overview,
  }), [open, openCopilot, closeCopilot, toggleCopilot, activeScreen, activePrompt,
      running, activeJob, currentRun, history, runAgentJob]);

  return <CopilotContext.Provider value={value}>{children}</CopilotContext.Provider>;
}

export function useCopilot() {
  const ctx = useContext(CopilotContext);
  if (!ctx) throw new Error('useCopilot must be used inside <CopilotProvider>');
  return ctx;
}
