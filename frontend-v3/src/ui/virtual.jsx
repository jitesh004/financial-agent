/* ────────────────────────────────────────────────────────────────────────────
   High-Performance Row Windowing & Virtualization
   ──────────────────────────────────────────────────────────────────────── */

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
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(measure);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onScroll) : null;
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
    virtual: enabled && count > 40,
  };
}

export function VirtualBody({
  count,
  items,
  rowHeight,
  containerRef,
  columns,
  children,
  renderRow,
  as = 'tbody',
  enabled = true,
}) {
  const totalCount = items ? items.length : count;
  const v = useVirtual({ count: totalCount, rowHeight, containerRef, enabled });
  const start = v.virtual ? v.start : 0;
  const end = v.virtual ? v.end : totalCount;

  const rows = [];
  for (let i = start; i < end; i += 1) {
    if (renderRow && items) {
      rows.push(renderRow(items[i], i));
    } else if (children) {
      rows.push(children(i));
    }
  }

  if (as === 'div') {
    return (
      <div className="virtual-div-container">
        {v.virtual && v.padTop > 0 && <div style={{ height: v.padTop }} aria-hidden="true" />}
        {rows}
        {v.virtual && v.padBottom > 0 && <div style={{ height: v.padBottom }} aria-hidden="true" />}
      </div>
    );
  }

  return (
    <tbody>
      {v.virtual && v.padTop > 0 && (
        <tr aria-hidden="true" style={{ height: v.padTop }}>
          <td colSpan={columns || 1} style={{ padding: 0, border: 0 }} />
        </tr>
      )}
      {rows}
      {v.virtual && v.padBottom > 0 && (
        <tr aria-hidden="true" style={{ height: v.padBottom }}>
          <td colSpan={columns || 1} style={{ padding: 0, border: 0 }} />
        </tr>
      )}
    </tbody>
  );
}
