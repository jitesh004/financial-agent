/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Topbar Navigation & Omni-Triggers
   ──────────────────────────────────────────────────────────────────────── */

import React from 'react';
import { Icon } from '../ui/icons';
import { useCopilot } from '../core/copilot';

export default function Topbar({
  title = 'Overview',
  onMenu,
  onCommand,
  onImport,
  importBadge = 0,
}) {
  const { toggleCopilot, running } = useCopilot();

  return (
    <header className="topbar">
      {/* Left: Mobile Trigger & Title */}
      <div className="topbar-left">
        <button
          type="button"
          className="btn btn-ghost btn-sm md-hidden"
          onClick={onMenu}
          aria-label="Open Navigation"
          style={{ display: 'none' }}
        >
          <Icon name="menu" size={18} />
        </button>
        <span className="topbar-title">{title}</span>

        {/* Global Omni-Search Trigger */}
        <button
          type="button"
          className="topbar-search-trigger"
          onClick={onCommand}
          title="Search anything (⌘K or /)"
        >
          <Icon name="search" size={14} />
          <span>Quick actions & search...</span>
          <span className="topbar-search-kbd">⌘K</span>
        </button>
      </div>

      {/* Right: Copilot & Import Actions */}
      <div className="topbar-right">
        {/* Omni-Copilot Trigger */}
        <button
          type="button"
          className="copilot-trigger-btn"
          onClick={toggleCopilot}
          title="Open AI Financial Copilot (⌘J)"
        >
          <Icon name="sparkles" size={15} />
          <span>Copilot</span>
          {running && <span className="beacon-live" style={{ background: '#ffffff', marginLeft: 2 }} />}
        </button>

        {/* Import Statements Trigger */}
        <button
          type="button"
          className="btn btn-secondary btn-sm flex items-center gap-2"
          onClick={onImport}
        >
          <Icon name="upload" size={14} />
          <span>Import</span>
          {importBadge > 0 && (
            <span className="badge badge-accent tabular-nums" style={{ fontSize: 11, padding: '1px 6px' }}>
              {importBadge}
            </span>
          )}
        </button>
      </div>
    </header>
  );
}
