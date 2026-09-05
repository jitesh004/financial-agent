import React, { useState } from 'react';
import Categorize from './Categorize';
import ReviewQueue from './ReviewQueue';

/* Triage, in one tab with two modes.
 *
 * "Review" and "Categorize" were separate tabs over the same queue of rows
 * nothing could classify confidently. They differ in how you work through it -
 * one row at a time with full context, or many at once by merchant - not in
 * what they are working on, and splitting them meant finishing one tab left
 * the other still showing a backlog. */

const MODES = [
  {
    key: 'queue', label: 'One at a time',
    hint: 'Each row with its context, for the ones that need a judgement call.',
  },
  {
    key: 'bulk', label: 'By merchant',
    hint: 'Group identical merchants and categorise them in one go.',
  },
];

const STORAGE_KEY = 'fa-review-mode';

export default function ReviewHub({ onDecided }) {
  const [mode, setMode] = useState(
    () => localStorage.getItem(STORAGE_KEY) || 'queue');

  const pick = (key) => {
    setMode(key);
    try { localStorage.setItem(STORAGE_KEY, key); } catch { /* private mode */ }
  };

  const active = MODES.find((m) => m.key === mode) || MODES[0];

  return (
    <>
      <div className="seg" style={{ marginBottom: 12 }}>
        {MODES.map((one) => (
          <button key={one.key} title={one.hint}
            className={`seg-btn ${mode === one.key ? 'active' : ''}`}
            onClick={() => pick(one.key)}>
            {one.label}
          </button>
        ))}
      </div>
      <div className="xp-hint" style={{ textTransform: 'none', marginBottom: 12 }}>
        {active.hint}
      </div>

      {mode === 'queue'
        ? <ReviewQueue onDecided={onDecided} />
        : <Categorize onDecided={onDecided} />}
    </>
  );
}
