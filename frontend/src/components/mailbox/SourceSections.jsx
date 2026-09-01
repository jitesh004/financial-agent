import React from 'react';
import Upload from '../Upload';
import { Callout, Chip } from '../ui';

/* Step 1: what to look for, and how far back — per source.
 *
 * One shared "look back" was wrong for every source at once. A holdings
 * statement is a photograph of what you own on one date, so last quarter's is
 * history; a bank statement from the same month is money still to be
 * accounted for; and alerts are capped at two months whatever anyone asks
 * for, because they are unreconciled by nature. One setting could only ever
 * be right for one of them.
 *
 * Uploading is the last section rather than a separate screen: a file from
 * this computer is another source of the same documents, and it goes through
 * the same Review before any of it counts.
 */

const CAPS = [250, 500, 1000, 2500, 5000];

/* The windows the server offers, plus whatever this source is actually set
   to. A <select> cannot display a value that is not among its options - it
   silently shows the first one instead, which is how a 2-month cap came to
   read "1 month".
 *
   The list itself comes from the server (`GET /api/gmail/periods`), which is
   the only place it is decided. A local copy lived here and had already
   drifted from it: it was missing "3 years" and "10 years" entirely, and
   relabelled the server's "1 year" as "12 months". */
function periodsFor(periods, months) {
  const list = periods?.length ? periods : [{ label: 'Everything', months: null }];
  if (months == null || list.some((p) => p.months === months)) return list;
  const extra = { label: `${months} month${months === 1 ? '' : 's'}`, months };
  return [...list, extra].sort((a, b) => (a.months ?? 1e9) - (b.months ?? 1e9));
}

export default function SourceSections({
  intents, periods, chosen, onToggle, settingsFor, onSetting, sections,
  onUploaded, onFilesChange,
}) {
  const staged = Object.fromEntries(
    (sections || []).map((s) => [s.key, s]));

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <p style={{ color: 'var(--text-2)', fontSize: 13, margin: 0 }}>
        Tick the sources to scan and set how far back each should look. They
        are scanned one after another, and you can re-scan any one of them on
        its own later.
      </p>

      {intents.map((one) => {
        const on = chosen.has(one.key);
        const capped = one.max_months != null;
        const settings = settingsFor(one.key);
        const count = staged[one.key];
        return (
          <div key={one.key} className="file-group" style={{ padding: '10px 12px' }}>
            <label style={{
              display: 'grid', gridTemplateColumns: 'auto 1fr auto',
              gap: 10, alignItems: 'start', cursor: 'pointer',
            }}>
              <input type="checkbox" checked={on}
                onChange={() => onToggle(one.key)} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{one.label}</div>
                <div className="xp-hint" style={{ textTransform: 'none', marginTop: 2 }}>
                  {one.description}
                </div>
              </div>
              {count?.staged > 0 && (
                <Chip>{count.staged} staged</Chip>
              )}
            </label>

            {on && (
              <div style={{
                display: 'flex', gap: 12, flexWrap: 'wrap',
                alignItems: 'flex-end', margin: '10px 0 0 26px',
              }}>
                <label>
                  <div style={{ fontSize: 12, fontWeight: 550, marginBottom: 4 }}>
                    Look back
                  </div>
                  <select className="xp-select"
                    value={settings.months ?? ''}
                    onChange={(e) => onSetting(one.key, {
                      months: e.target.value ? Number(e.target.value) : null,
                    })}>
                    {/* A capped source's own limit has to be one of the
                        options, or the select cannot show it: alerts are
                        capped at 2 months, the list offered 1/3/6/12, and the
                        browser fell back to the first - displaying "1 month"
                        for a scan that would read 2. */}
                    {periodsFor(periods, settings.months).map((p) => (
                      <option key={p.label} value={p.months ?? ''}>{p.label}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <div style={{ fontSize: 12, fontWeight: 550, marginBottom: 4 }}>
                    Max emails to read
                  </div>
                  <select className="xp-select" value={settings.maxMessages}
                    onChange={(e) => onSetting(one.key, {
                      maxMessages: Number(e.target.value),
                    })}>
                    {CAPS.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
                {capped && (
                  <div className="xp-hint" style={{ textTransform: 'none', maxWidth: 340 }}>
                    Starts at {one.max_months} months because these are
                    unreconciled and earn their place by being fresher than the
                    statement covering them — older ones are mostly superseded.
                    Widen it if you want to.
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}

      <div className="file-group" style={{ padding: '10px 12px' }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>
          Files from this computer
        </div>
        <div className="xp-hint" style={{ textTransform: 'none', marginBottom: 8 }}>
          Anything Gmail does not carry. Add them here and they are read on
          the next step with every other source — a file from your computer is
          just another source.
          {staged.upload?.staged > 0 && ` ${staged.upload.staged} staged so far.`}
        </div>
        <Upload compact onComplete={onUploaded} onFilesChange={onFilesChange} />
      </div>

      {chosen.size === 0 && (
        <Callout tone="warn">
          Nothing is ticked, so a scan has nothing to look for. Pick at least
          one source — or just add files above.
        </Callout>
      )}
    </div>
  );
}
