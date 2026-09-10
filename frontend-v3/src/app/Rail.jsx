/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Modern Navigation Rail
   ──────────────────────────────────────────────────────────────────────── */

import React from 'react';
import { GROUPS, ROUTES } from './routes';
import { Link, useRouter } from '../core/router';
import { Icon, Logo } from '../ui/icons';
import { readTheme, setTheme } from '../core/theme';
import { useAuth } from '../core/auth';

export default function Rail({
  collapsed,
  onToggle,
  open,
  onClose,
  hasLedger,
  reviewCount,
}) {
  const { isAdmin, user } = useAuth();
  const { path: currentPath } = useRouter();
  const currentTheme = readTheme();

  const toggleTheme = () => {
    setTheme(currentTheme === 'dark' ? 'light' : 'dark');
  };

  return (
    <aside className={`rail ${open ? 'drawer-open' : ''}`}>
      {/* Brand Header */}
      <div className="rail-head">
        <Link to="/" className="rail-brand" onClick={onClose}>
          <div className="rail-brand-icon">
            <Logo size={18} />
          </div>
          {!collapsed && (
            <>
              <span>PRISM</span>
              <span className="rail-brand-version">v3</span>
            </>
          )}
        </Link>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={onToggle}
          title={collapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
          style={{ padding: 4, color: 'var(--text-3)' }}
        >
          <Icon name={collapsed ? 'chevron' : 'chevron-left'} size={15} />
        </button>
      </div>

      {/* Navigation Groups */}
      <div className="rail-scroll">
        {GROUPS.map((group) => {
          const groupRoutes = ROUTES.filter((r) => r.group === group.key && !r.hidden && (!r.adminOnly || isAdmin));
          if (!groupRoutes.length) return null;

          return (
            <div key={group.key} className="rail-group">
              <div className="rail-group-label">{group.label}</div>
              {groupRoutes.map((route) => {
                const isActive = currentPath === route.path || (route.path !== '/' && currentPath.startsWith(`${route.path}/`));
                const isBlocked = route.needsLedger && !hasLedger;
                const badgeCount = route.badge === 'review' ? reviewCount : null;

                return (
                  <Link
                    key={route.key}
                    to={route.path}
                    className={`rail-item ${isActive ? 'active' : ''} ${isBlocked ? 'opacity-60' : ''}`}
                    onClick={onClose}
                    title={route.title}
                  >
                    <div className="rail-item-icon">
                      <Icon name={route.icon} size={17} />
                    </div>
                    {!collapsed && (
                      <>
                        <span className="truncate">{route.label}</span>
                        {badgeCount > 0 && (
                          <span className="rail-item-badge tabular-nums">{badgeCount}</span>
                        )}
                      </>
                    )}
                  </Link>
                );
              })}
            </div>
          );
        })}
      </div>

      {/* Footer Controls */}
      <div className="rail-foot">
        <button
          type="button"
          className="btn btn-ghost btn-sm flex items-center gap-2"
          onClick={toggleTheme}
          title="Toggle Dark / Light Theme"
          style={{ width: collapsed ? '100%' : 'auto', justifyContent: collapsed ? 'center' : 'flex-start' }}
        >
          <Icon name={currentTheme === 'dark' ? 'sun' : 'moon'} size={16} />
          {!collapsed && <span>{currentTheme === 'dark' ? 'Light mode' : 'Dark mode'}</span>}
        </button>

        {!collapsed && user && (
          <Link to="/profile" className="btn btn-ghost btn-sm" title="Profile Credentials">
            <Icon name="user" size={15} />
          </Link>
        )}
      </div>
    </aside>
  );
}
