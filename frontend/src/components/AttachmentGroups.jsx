import React, { useMemo, useState } from 'react';
import { dateLabel, formatBytes } from '../lib';
import { Chip, Empty } from './ui';
import { CATEGORY_TONE } from './mailbox/parts';

/* Statement list grouped by institution.

   A flat list of 220 files across 23 banks is unusable: you cannot see which
   accounts are covered, spot a missing month, or exclude one institution
   without hunting. Grouping by sender and sorting each group by date turns it
   into something you can actually audit - each group reads as "this account,
   these months". */

const rowKey = (r) => `${r.message_id}:${r.filename}:${r.size}`;

export default function AttachmentGroups({
  rows, selected, onToggle, onToggleMany, dateOrder, onToggleDateOrder,
}) {
  const [collapsed, setCollapsed] = useState(() => new Set());

  const groups = useMemo(() => {
    // Group by INSTITUTION + account type, not by sender domain.
    //
    // Domains split one bank across several groups and label them with whatever
    // the mailbox display name happens to be: ICICI savings statements appeared
    // under a group called "Estatement", split across icicibank.com and
    // icici.bank.in, while ICICI cards showed as "credit_cards@icicibank.com".
    // Searching the list for "ICICI" found nothing.
    const map = new Map();
    for (const r of rows) {
      const institution = r.institution || r.sender_name || r.sender_domain || 'Unknown';
      const key = `${institution}::${r.category}`;
      if (!map.has(key)) {
        map.set(key, {
          key,
          name: institution,
          domain: r.sender_domain,
          domains: new Set(),
          items: [],
        });
      }
      const group = map.get(key);
      group.domains.add(r.sender_domain);
      group.items.push(r);
    }

    const dir = dateOrder === 'asc' ? 1 : -1;
    const out = [...map.values()].map((g) => {
      // Chronological within the group, so a missing month is visible as a gap.
      const items = [...g.items].sort(
        (a, b) => String(a.date_iso || '').localeCompare(String(b.date_iso || '')) * dir,
      );
      const dates = items.map((i) => i.date_iso).filter(Boolean).sort();
      return {
        ...g,
        items,
        bytes: items.reduce((s, i) => s + (i.size || 0), 0),
        cached: items.filter((i) => i.cached).length,
        missingPassword: items.filter((i) => !i.password_ready).length,
        category: items[0]?.category,
        first: dates[0],
        last: dates[dates.length - 1],
      };
    });

    // Biggest groups first: the accounts with the most history matter most.
    out.sort((a, b) => b.items.length - a.items.length);
    return out;
  }, [rows, dateOrder]);

  if (!rows.length) {
    return <Empty title="Nothing matches these filters">Adjust the type or sender filters above.</Empty>;
  }

  const toggleCollapse = (key) => setCollapsed((prev) => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  });

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
        <span style={{ color: 'var(--text-3)' }}>
          {groups.length} institution{groups.length === 1 ? '' : 's'}
        </span>
        <button className="btn" style={{ padding: '3px 10px', fontSize: 12 }}
          onClick={onToggleDateOrder}>
          Date {dateOrder === 'asc' ? 'oldest first ↑' : 'newest first ↓'}
        </button>
        <button className="btn" style={{ padding: '3px 10px', fontSize: 12 }}
          onClick={() => setCollapsed(collapsed.size ? new Set() : new Set(groups.map((g) => g.key)))}>
          {collapsed.size ? 'Expand all' : 'Collapse all'}
        </button>
      </div>

      {/* Flex column, NOT grid. A grid container with a definite height sizes
          its auto rows to fit that height, so 12 groups were each squashed to
          36px and their expanded tables clipped away by overflow:hidden. Flex
          items with flexShrink:0 keep their content height and let the
          container scroll, which is the behaviour a list actually wants. */}
      <div className="scroll-y" style={{
        maxHeight: 520, display: 'flex', flexDirection: 'column', gap: 8,
      }}>
        {groups.map((group) => {
          const isCollapsed = collapsed.has(group.key);
          const chosen = group.items.filter((i) => selected.has(rowKey(i))).length;
          const allChosen = chosen === group.items.length;

          return (
            <div key={group.key} style={{
              border: '1px solid var(--border)', borderRadius: 8,
              background: 'var(--surface)', overflow: 'hidden',
              flexShrink: 0,
            }}>
              {/* ---- Group header ---- */}
              {/* Two-row header: identity on the left, stats wrapping on the
                  right. A single flex row clipped the date range behind the
                  chips at anything under a very wide viewport. */}
              <div style={{
                display: 'flex', gap: 10, alignItems: 'flex-start',
                padding: '10px 12px', background: 'var(--surface-2)',
                borderBottom: isCollapsed ? 0 : '1px solid var(--border)',
                flexWrap: 'wrap',
              }}>
                <input
                  type="checkbox"
                  checked={allChosen}
                  ref={(el) => { if (el) el.indeterminate = chosen > 0 && !allChosen; }}
                  onChange={() => onToggleMany(group.items, !allChosen)}
                  title={allChosen ? 'Deselect this institution' : 'Select this institution'}
                  style={{ marginTop: 3 }}
                />

                <button
                  onClick={() => toggleCollapse(group.key)}
                  className="btn"
                  style={{ border: 0, background: 'transparent', padding: 2, fontSize: 12, lineHeight: 1 }}
                  aria-label={isCollapsed ? 'Expand' : 'Collapse'}
                >
                  {isCollapsed ? '▸' : '▾'}
                </button>

                <div style={{ flex: '1 1 240px', minWidth: 0 }}>
                  <div style={{
                    fontWeight: 620, fontSize: 13.5, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {group.name}
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 2 }}>
                    {group.first && <>{dateLabel(group.first)} → {dateLabel(group.last)} · </>}
                    {[...(group.domains || [])].filter(Boolean).join(', ')}
                  </div>
                </div>

                <div style={{
                  display: 'flex', gap: 6, alignItems: 'center',
                  flexWrap: 'wrap', justifyContent: 'flex-end',
                }}>
                  <Chip tone={CATEGORY_TONE[group.category]}>{group.category}</Chip>
                  <Chip tone={allChosen ? 'accent' : ''}>
                    {chosen}/{group.items.length}
                  </Chip>
                  <span className="num" style={{ fontSize: 12, color: 'var(--text-3)' }}>
                    {formatBytes(group.bytes)}
                  </span>
                  {group.cached > 0 && <Chip tone="pos">{group.cached} cached</Chip>}
                  {group.missingPassword > 0 && (
                    <Chip tone="warn">{group.missingPassword} need details</Chip>
                  )}
                </div>
              </div>

              {/* ---- Group rows ---- */}
              {!isCollapsed && (
                // `.table-wrap` carries negative margins so a table can bleed to
                // a card's edges. Inside this group box - which clips with
                // overflow:hidden to keep its rounded corners - those margins
                // push the table outside the clip and it vanishes entirely.
                // Reset them here rather than weakening the shared class.
                <div className="table-wrap" style={{ margin: 0, padding: 0 }}>
                  {/* Fixed layout so the declared column widths are honoured.
                      With auto layout the browser widened File/Subject to fit
                      their content, pushing the checkbox column off the left
                      edge behind a horizontal scrollbar. */}
                  <table style={{ tableLayout: 'fixed', width: '100%' }}>
                    <thead>
                      <tr>
                        <th style={{ width: 36 }} />
                        <th style={{ width: 84 }}>Date</th>
                        <th style={{ width: '32%' }}>File</th>
                        <th style={{ width: '32%' }}>Subject</th>
                        <th style={{ width: 120 }}>Password</th>
                        <th className="right" style={{ width: 68 }}>Size</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.items.map((r) => {
                        const checked = selected.has(rowKey(r));
                        return (
                          <tr key={rowKey(r)} style={{ opacity: checked ? 1 : 0.5 }}>
                            <td>
                              <input type="checkbox" checked={checked}
                                onChange={() => onToggle(r)} />
                            </td>
                            <td className="nowrap num" style={{ fontSize: 12 }}>
                              {r.date_iso ? dateLabel(r.date_iso) : '—'}
                            </td>
                            <td>
                              <div className="truncate" style={{ maxWidth: 190 }} title={r.filename}>
                                {r.filename}
                              </div>
                              {r.cached && <Chip tone="pos">cached</Chip>}
                            </td>
                            <td>
                              <div className="truncate" style={{ maxWidth: 190 }} title={r.subject}>
                                {r.subject}
                              </div>
                            </td>
                            <td>
                              <div title={r.password_explanation} style={{ cursor: 'help' }}>
                                <Chip tone={r.password_ready ? 'pos' : 'warn'}>
                                  {r.password_rule}
                                </Chip>
                              </div>
                            </td>
                            <td className="right num nowrap">{formatBytes(r.size)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
