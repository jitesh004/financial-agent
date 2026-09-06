/* Routing.
 *
 * The app this replaces kept the current screen in `useState`. That is one
 * line of code and four missing features: the back button left the app, a
 * screen could not be linked to, a reload always landed on Overview, and
 * opening a second tab on a different tab was impossible.
 *
 * This is the History API, exposed as three things a component actually wants:
 * `useRoute()` to read where you are, `navigate()` to go somewhere, and
 * `<Link>` to render something that goes there - as a real anchor, so
 * middle-click and "open in new tab" work, which no onClick handler can give
 * you.
 *
 * Query parameters are part of the route on purpose. A filtered ledger, an
 * open dashboard, a selected month: each is a thing somebody will want to send
 * to themselves later, and `useRouteParam` makes putting one in the URL as
 * cheap as putting it in state.
 */

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';

const RouterContext = createContext(null);

function readLocation() {
  return {
    path: window.location.pathname.replace(/\/+$/, '') || '/',
    search: window.location.search,
    hash: window.location.hash,
  };
}

export function RouterProvider({ children }) {
  const [loc, setLoc] = useState(readLocation);

  useEffect(() => {
    const onPop = () => setLoc(readLocation());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((to, { replace = false, scroll = true } = {}) => {
    const url = typeof to === 'string' ? to : buildUrl(to);
    const current = window.location.pathname + window.location.search;
    if (url === current) return;
    window.history[replace ? 'replaceState' : 'pushState']({}, '', url);
    setLoc(readLocation());
    if (scroll) {
      // The scroll container is the main column, not the document - so
      // `window.scrollTo` does nothing and a new screen opens half way down
      // the previous one.
      document.querySelector('.main')?.scrollTo({ top: 0, behavior: 'auto' });
    }
  }, []);

  const value = useMemo(() => ({ ...loc, navigate }), [loc, navigate]);

  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

function buildUrl({ path, params }) {
  const search = params ? new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== '' && v != null),
  ).toString() : '';
  return `${path}${search ? `?${search}` : ''}`;
}

export function useRouter() {
  const value = useContext(RouterContext);
  if (!value) throw new Error('useRouter must be used inside <RouterProvider>');
  return value;
}

/** `{ path, params, navigate }` - params as a plain object. */
export function useRoute() {
  const { path, search, navigate } = useRouter();
  const params = useMemo(
    () => Object.fromEntries(new URLSearchParams(search)),
    [search],
  );
  return { path, params, navigate };
}

/**
 * One query parameter, read and written like state.
 *
 * Writing replaces rather than pushes: flipping a filter is not a navigation
 * anybody wants twelve entries of in their back button, but the resulting URL
 * is still the shareable one.
 */
export function useRouteParam(name, fallback = '') {
  const { path, params, navigate } = useRoute();
  const value = params[name] ?? fallback;
  const set = useCallback((next) => {
    const merged = { ...params };
    if (next === '' || next == null || next === fallback) delete merged[name];
    else merged[name] = next;
    navigate({ path, params: merged }, { replace: true, scroll: false });
  }, [path, params, navigate, name, fallback]);
  return [value, set];
}

/**
 * A real anchor that navigates in-app.
 *
 * Modified clicks (⌘, ctrl, shift, middle button) and any explicit target fall
 * through to the browser, so "open in a new tab" opens a new tab.
 */
export const Link = React.forwardRef(function Link(
  { to, params, replace, onClick, children, ...rest }, ref,
) {
  const { navigate } = useRouter();
  const href = typeof to === 'string' && !params ? to : buildUrl({ path: to, params });
  const handle = (e) => {
    onClick?.(e);
    if (e.defaultPrevented) return;
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (rest.target && rest.target !== '_self') return;
    e.preventDefault();
    navigate(href, { replace });
  };
  return <a ref={ref} href={href} onClick={handle} {...rest}>{children}</a>;
});

/** True when `path` is the current route, or a parent of it. */
export function useIsActive(path) {
  const { path: here } = useRouter();
  return here === path || here.startsWith(`${path}/`);
}
