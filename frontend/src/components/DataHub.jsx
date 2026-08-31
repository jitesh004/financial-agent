import React, { useState } from 'react';
import DataManager from './DataManager';
import Files from './Files';
import FilesAndPasswords from './FilesAndPasswords';

/* File bookkeeping, in one place instead of three tabs.
 *
 * "Files & quality", "Files & Passwords" and "Data" were three separate nav
 * entries about the same subject: which files came in, what happened to them,
 * and what to do about the ones that failed. Three tabs meant the answer to
 * "why is this statement missing?" lived somewhere you had to guess. */

const SECTIONS = [
  {
    key: 'quality', label: 'Coverage & quality',
    hint: 'Which months each account has statements for, and which parses '
      + 'reconciled.',
  },
  {
    key: 'registry', label: 'Files & passwords',
    hint: 'Every file ever attempted, whatever happened to it - including the '
      + 'ones that are still locked.',
  },
  {
    key: 'manage', label: 'Manage data',
    hint: 'Snapshots, clearing scopes, and starting over.',
  },
];

const STORAGE_KEY = 'fa-datahub-section';

export default function DataHub({ data }) {
  const [section, setSection] = useState(
    () => localStorage.getItem(STORAGE_KEY) || 'quality');

  const pick = (key) => {
    setSection(key);
    try { localStorage.setItem(STORAGE_KEY, key); } catch { /* private mode */ }
  };

  const active = SECTIONS.find((s) => s.key === section) || SECTIONS[0];

  return (
    <>
      <div className="seg" style={{ marginBottom: 12 }}>
        {SECTIONS.map((one) => (
          <button key={one.key} title={one.hint}
            className={`seg-btn ${section === one.key ? 'active' : ''}`}
            onClick={() => pick(one.key)}>
            {one.label}
          </button>
        ))}
      </div>
      <div className="xp-hint" style={{ textTransform: 'none', marginBottom: 12 }}>
        {active.hint}
      </div>

      {section === 'quality' && <Files data={data} />}
      {section === 'registry' && <FilesAndPasswords />}
      {section === 'manage' && <DataManager />}
    </>
  );
}
