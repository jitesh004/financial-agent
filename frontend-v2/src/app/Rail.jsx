/* The navigation rail.
 *
 * A vertical rail rather than the horizontal strip this replaces, for one
 * measurable reason: there are eighteen destinations, and at a 1264px laptop
 * width nine of them were off the edge of a strip - including Review with 67
 * rows waiting. The strip scrolled, with a hidden scrollbar and a fade mask,
 * so the hidden entries did not read as cut off. They read as absent.
 *
 * Vertical space is the thing there is plenty of. Every destination is on
 * screen at once, grouped by the question it answers, and the whole rail
 * collapses to icons when the window is narrow enough for that to matter.
 *
 * Each entry prefetches its screen's first request on hover. By the time the
 * click lands the data is usually already in the cache, which is most of why
 * navigation here feels instant.
 */

import React from 'react';
import { Link, useRouter } from '../core/router';
import { useAuth } from '../core/auth';
import { GROUPS, ROUTES } from './routes';
import { Icon, Logo } from '../ui/icons';
import { IconButton } from '../ui';
import { prefetch } from '../core/store';
import { api } from '../core/api';

/* What a screen needs before it can paint. Warmed on hover; anything not
   listed simply loads when it is opened. */
const WARM = {
  overview: ['dashboard', () => api.dashboard()],
  spending: ['dashboard', () => api.dashboard()],
  debt: ['dashboard', () => api.dashboard()],
  forecast: ['dashboard', () => api.dashboard()],
  months: ['dashboard', () => api.dashboard()],
  recurring: ['recurring', () => api.recurring()],
  position: ['position', () => api.position(true)],
  portfolio: ['portfolio', () => api.portfolio()],
  credit: ['bureau', () => api.bureau()],
  owed: ['claims', () => api.claims()],
  agents: ['agents', () => api.agents()],
  explore: ['boards', () => api.boards()],
  rules: ['rules', () => api.rules()],
  data: ['files', () => api.files()],
  settings: ['settings', () => api.settings()],
  admin: ['admin', () => api.adminOverview()],
};

export default function Rail({
  collapsed, onToggle, open, onClose, hasLedger, reviewCount,
}) {
  const { path } = useRouter();
  const { isAdmin } = useAuth();

  const visible = ROUTES.filter((r) => !r.hidden
    && (!r.adminOnly || isAdmin)
    && (!r.needsLedger || hasLedger));

  const warm = (key) => {
    const spec = WARM[key];
    if (spec) prefetch(spec[0], spec[1]);
    // The route's own chunk, too - a lazy import that starts on hover has
    // usually resolved before the click.
    ROUTES.find((r) => r.key === key)?.load?.();
  };

  return (
    <nav className={`rail ${open ? 'open' : ''}`} aria-label="Sections">
      <div className="rail-head">
        <Link to="/" className="rail-brand" onClick={onClose}>
          <Logo />
          <span>Prism</span>
        </Link>
        <span className="spacer" />
        <IconButton
          icon={collapsed ? 'chevron' : 'panel'}
          label={collapsed ? 'Expand the navigation' : 'Collapse the navigation'}
          className="ghost sm"
          onClick={onToggle}
        />
      </div>

      <div className="rail-scroll">
        {GROUPS.map((group) => {
          const members = visible.filter((r) => r.group === group.key);
          // A group with nothing available in it is not shown at all: before
          // an import, Money and Transactions have no members, and an empty
          // group is a dead end wearing the same clothes as a live one.
          if (!members.length) return null;
          return (
            <div className="rail-group" key={group.key}>
              <div className="rail-group-label">{group.label}</div>
              {members.map((r) => {
                const active = path === r.path;
                const badge = r.badge === 'review' ? reviewCount : 0;
                return (
                  <Link
                    key={r.key}
                    to={r.path}
                    className="rail-item"
                    aria-current={active ? 'page' : undefined}
                    title={collapsed ? `${r.label} — ${r.blurb}` : r.blurb}
                    onMouseEnter={() => warm(r.key)}
                    onFocus={() => warm(r.key)}
                    onClick={onClose}
                  >
                    <Icon name={r.icon} size={16} />
                    <span className="rail-label">{r.label}</span>
                    {badge > 0 && <span className="rail-count">{badge}</span>}
                  </Link>
                );
              })}
            </div>
          );
        })}
      </div>
    </nav>
  );
}
