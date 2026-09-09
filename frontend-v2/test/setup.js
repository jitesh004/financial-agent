/* What jsdom does not have, and this app expects the browser to.
 *
 * Every polyfill here stands in for something a real browser provides and
 * jsdom does not. None of them fake app behaviour: `ResizeObserver` never
 * fires, which is exactly what a browser does for an element that never
 * changes size, and the virtualiser's own fallback path is what runs.
 */

import { afterEach, beforeEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

/* The virtualiser and the horizontal-scroll fade both observe an element. */
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}

    unobserve() {}

    disconnect() {}
  };
}

/* Charts animate on a frame; jsdom has rAF, but not always in every version.
   Callbacks run on a macrotask so a test can await them. */
if (!globalThis.requestAnimationFrame) {
  globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
}

/* jsdom lays nothing out, so every element measures 0x0 - and every chart in
   this app draws nothing until it has measured its container (ui/charts.jsx,
   `useSize`). Reporting a plausible box is what lets a test assert that a
   chart drew, rather than only that it did not throw. */
for (const [prop, value] of [['clientWidth', 800], ['offsetWidth', 800],
  ['clientHeight', 600], ['offsetHeight', 600]]) {
  const existing = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop);
  Object.defineProperty(HTMLElement.prototype, prop, {
    configurable: true,
    get() {
      const own = existing?.get?.call(this) ?? 0;
      return own || value;
    },
  });
}

/* The theme reads this before React renders anything. */
if (!window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}

/* Every CSV export goes through these two. jsdom implements neither, and an
   export that throws would fail the test for the wrong reason. */
if (!URL.createObjectURL) URL.createObjectURL = () => 'blob:test';
if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};

/* `Element.scrollTo` is a no-op in jsdom but has to exist: the router calls it
   on the main column after every navigation. */
if (!Element.prototype.scrollTo) Element.prototype.scrollTo = function scrollTo() {};

/* Downloads: a real anchor click on a blob: href makes jsdom complain about
   navigation it cannot perform. The click itself is what tests assert on, so
   it is recorded rather than performed. */
export const downloads = [];
beforeEach(() => {
  downloads.length = 0;
  const realClick = HTMLAnchorElement.prototype.click;
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() {
    if (this.download) {
      downloads.push({ filename: this.download, href: this.href });
      return;
    }
    realClick.call(this);
  });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  // Each test starts at the app root; a route left over from the previous one
  // would silently change which screen a shell test renders.
  window.history.replaceState({}, '', '/');
});
