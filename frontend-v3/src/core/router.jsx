/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 History Router
   URL History API navigation with bookmarkable search params.
   ──────────────────────────────────────────────────────────────────────── */

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
      document.querySelector('.page-container')?.scrollTo({ top: 0, behavior: 'auto' });
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

export function useRoute() {
  const { path, search, navigate } = useRouter();
  const params = useMemo(
    () => Object.fromEntries(new URLSearchParams(search)),
    [search],
  );
  return { path, params, navigate };
}

export function useSetRouteParams() {
  const { navigate } = useRouter();
  return useCallback((changes) => {
    const merged = new URLSearchParams(window.location.search);
    for (const [name, next] of Object.entries(changes)) {
      if (next === '' || next == null) merged.delete(name);
      else merged.set(name, next);
    }
    const search = merged.toString();
    const path = window.location.pathname.replace(/\/+$/, '') || '/';
    navigate(`${path}${search ? `?${search}` : ''}`, { replace: true, scroll: false });
  }, [navigate]);
}

export function useRouteParam(name, fallback = '') {
  const { params } = useRoute();
  const setParams = useSetRouteParams();
  const value = params[name] ?? fallback;
  const set = useCallback(
    (next) => setParams({ [name]: next === fallback ? '' : next }),
    [setParams, name, fallback],
  );
  return [value, set];
}

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

export function useIsActive(path) {
  const { path: here } = useRouter();
  return here === path || here.startsWith(`${path}/`);
}
