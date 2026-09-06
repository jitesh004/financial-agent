/* Row windowing.
 *
 * A ledger page is a few hundred rows and a drill-down can be five thousand.
 * Rendering all of them costs a second of layout on a laptop and makes every
 * subsequent keystroke in the filter box feel broken, so only the rows inside
 * the viewport (plus a margin) are mounted.
 *
 * Deliberately fixed-height rather than measured. Measuring each row is what
 * makes a virtualiser complicated and janky - it has to render, measure,
 * correct, and re-render - and every row in this app is one of two known
 * heights, chosen by the density preference. Padding rows above and below keep
 * the scrollbar honest, so nothing about the scroll position is faked.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';

const OVERSCAN = 8;

export function useVirtual({ count, rowHeight, containerRef, enabled = true }) {
  const [range, setRange] = useState({ start: 0, end: Math.min(count, 40) });
  const frame = useRef(0);

  const measure = useCallback(() => {
    const el = containerRef.current;
    if (!el || !enabled) {
      setRange({ start: 0, end: count });
      return;
    }
    const top = el.scrollTop;
    const height = el.clientHeight || 600;
    const start = Math.max(0, Math.floor(top / rowHeight) - OVERSCAN);
    const end = Math.min(count, Math.ceil((top + height) / rowHeight) + OVERSCAN);
    setRange((prev) => (prev.start === start && prev.end === end ? prev : { start, end }));
  }, [containerRef, count, rowHeight, enabled]);

  useEffect(() => {
    measure();
    const el = containerRef.current;
    if (!el || !enabled) return undefined;
    const onScroll = () => {
      // One measurement per frame. A scroll event fires far more often than
      // the screen repaints, and setState per event is what makes a
      // virtualiser stutter on the very lists it exists to speed up.
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(measure);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    const ro = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(onScroll) : null;
    ro?.observe(el);
    return () => {
      cancelAnimationFrame(frame.current);
      el.removeEventListener('scroll', onScroll);
      ro?.disconnect();
    };
  }, [measure, containerRef, enabled]);

  return {
    ...range,
    padTop: range.start * rowHeight,
    padBottom: Math.max(0, (count - range.end) * rowHeight),
    virtual: enabled && count > 60,
  };
}

/** A `<tbody>` that only mounts the rows in view, with spacer rows keeping the
    scroll height correct. */
export function VirtualBody({ count, rowHeight, containerRef, columns, children, enabled = true }) {
  const v = useVirtual({ count, rowHeight, containerRef, enabled });
  const start = v.virtual ? v.start : 0;
  const end = v.virtual ? v.end : count;
  const rows = [];
  for (let i = start; i < end; i += 1) rows.push(children(i));
  return (
    <tbody>
      {v.virtual && v.padTop > 0 && (
        <tr aria-hidden="true" style={{ height: v.padTop }}>
          <td colSpan={columns} style={{ padding: 0, border: 0 }} />
        </tr>
      )}
      {rows}
      {v.virtual && v.padBottom > 0 && (
        <tr aria-hidden="true" style={{ height: v.padBottom }}>
          <td colSpan={columns} style={{ padding: 0, border: 0 }} />
        </tr>
      )}
    </tbody>
  );
}
