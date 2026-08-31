import React, { useEffect, useMemo, useState } from 'react';
import { colorFor, compact, dateLabel, money, titleCase } from '../lib';
import { BarList, Callout, Card, Chip, Empty, Stat } from './ui';
import { api } from '../lib';

/* What you own, read off the holdings statements rather than off a price feed.
 *
 * Every figure here is units x NAV as printed on a statement that reconciled
 * against its own declared total. No live prices: this app has no way to
 * verify one, and a net worth that changes when you reload it cannot be
 * checked against anything later. */

const KIND_LABEL = {
  equity: 'Equity', mutual_fund: 'Mutual funds', etf: 'ETFs',
  bond: 'Bonds & debt', other: 'Other',
};

export default function Portfolio({ onImport }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [sort, setSort] = useState({ key: 'value', dir: 'desc' });

  useEffect(() => {
    api.request('/api/portfolio')
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  const holdings = useMemo(() => {
    const rows = data?.holdings || [];
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = Number(a[sort.key]) || 0;
      const bv = Number(b[sort.key]) || 0;
      if (av || bv) return (av - bv) * dir;
      return String(a[sort.key] ?? '').localeCompare(String(b[sort.key] ?? '')) * dir;
    });
  }, [data, sort]);

  if (error) return <Callout tone="warn">{error}</Callout>;
  if (!data) {
    return (
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: 40 }}>
        <div className="spinner" /> Loading…
      </div>
    );
  }

  if (!holdings.length) {
    return (
      <Empty title="No holdings imported yet"
        action={onImport && (
          <button className="btn primary" onClick={onImport}>
            Import statements
          </button>
        )}>
        Import a CAS from CDSL or NSDL, a CAMS or KFintech statement, or your
        broker&apos;s own holdings PDF. Every position is valued at the NAV the
        statement printed, and the total is checked against the one it declares.
      </Empty>
    );
  }

  const totals = data.totals || {};
  const gain = totals.gain != null ? Number(totals.gain) : null;
  const invested = Number(totals.invested) || 0;

  return (
    <>
      {data.unreconciled?.length > 0 && (
        <Callout tone="warn" style={{ marginBottom: 14 }}>
          <strong>
            {data.unreconciled.length} statement
            {data.unreconciled.length === 1 ? '' : 's'} did not add up.
          </strong>{' '}
          The holdings read out of{' '}
          {data.unreconciled.map((s) => s.filename).join(', ')} do not total the
          value the document declares, so the figures below may be incomplete.
          <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--text-2)' }}>
            {data.unreconciled[0].message}
          </div>
        </Callout>
      )}

      <div className="grid cols-4">
        <Stat label="Portfolio value" value={Number(totals.value) || 0} />
        <Stat label="Invested" value={invested || '—'} />
        <Stat
          label="Unrealised gain"
          value={gain == null ? '—' : gain}
          tone={gain == null ? '' : gain >= 0 ? 'pos' : 'neg'}
          /* Says what the figure covers. A demat statement prints no cost, so
             the gain can only ever speak for the holdings that declare one -
             and a percentage over a partial basis is worse than none. */
          note={gain == null ? 'No cost basis on these statements'
            : `${((gain / invested) * 100).toFixed(1)}% on cost`
              + (totals.uncosted_instruments
                ? ` · excludes ${totals.uncosted_instruments} holding`
                  + `${totals.uncosted_instruments === 1 ? '' : 's'} with no cost`
                : '')}
        />
        <Stat
          label="Instruments"
          value={String(totals.instruments ?? holdings.length)}
          note={totals.as_of ? `valued ${dateLabel(totals.as_of)}` : ''}
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Card title="By asset type">
          <BarList
            items={(data.by_kind || []).map((bucket, i) => ({
              label: KIND_LABEL[bucket.kind] || titleCase(bucket.kind),
              value: Number(bucket.value) || 0,
              color: colorFor(i),
            }))}
            total={Number(totals.value) || 0}
          />
        </Card>

        <Card title="Largest holdings" sub={`${holdings.length} in total`}>
          <BarList
            items={holdings.slice(0, 10).map((h, i) => ({
              label: h.instrument || h.symbol || h.isin,
              value: Number(h.value) || 0,
              color: colorFor(i),
            }))}
            total={Number(totals.value) || 0}
          />
        </Card>
      </div>

      <div className="section-title">Holdings</div>
      <Card>
        <div className="table-wrap scroll-y" style={{ maxHeight: 560 }}>
          <table>
            <thead>
              <tr>
                <th onClick={() => setSort({ key: 'instrument', dir: 'asc' })}
                  style={{ cursor: 'pointer' }}>Instrument</th>
                <th>Type</th>
                <th className="right">Units</th>
                <th className="right">NAV / price</th>
                <th className="right" onClick={() => setSort({
                  key: 'value', dir: sort.dir === 'desc' ? 'asc' : 'desc',
                })} style={{ cursor: 'pointer' }}>Value</th>
                <th className="right">Gain</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((h) => {
                const value = Number(h.value) || 0;
                const cost = Number(h.invested)
                  || (Number(h.units) || 0) * (Number(h.avg_cost) || 0);
                const delta = cost ? value - cost : null;
                return (
                  <tr key={h.id}>
                    <td>
                      <div className="truncate" style={{ maxWidth: 300 }}
                        title={h.instrument}>
                        {h.instrument || h.symbol || h.isin}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                        {h.isin}{h.folio ? ` · folio ${h.folio}` : ''}
                      </div>
                    </td>
                    <td><Chip>{KIND_LABEL[h.kind] || h.kind}</Chip></td>
                    <td className="right num">
                      {h.units ? Number(h.units).toLocaleString('en-IN',
                        { maximumFractionDigits: 3 }) : '—'}
                    </td>
                    <td className="right num">
                      {h.nav ? money(Number(h.nav), true) : '—'}
                    </td>
                    <td className="right num">{money(value)}</td>
                    <td className="right num">
                      {delta == null ? '—' : (
                        <span className={delta >= 0 ? 'pos' : 'neg'}>
                          {delta >= 0 ? '+' : ''}{compact(delta)}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {data.statements?.length > 0 && (
        <>
          <div className="section-title">Where these came from</div>
          <Card>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>File</th><th>Provider</th><th>Valued</th>
                    <th className="right">Declared</th>
                    <th className="right">Computed</th>
                    <th>Checks out?</th>
                  </tr>
                </thead>
                <tbody>
                  {data.statements.map((s) => (
                    <tr key={s.id}>
                      <td><div className="truncate" style={{ maxWidth: 240 }}>
                        {s.source_filename}
                      </div></td>
                      <td>{s.provider || s.layout}</td>
                      <td className="nowrap">{s.as_of ? dateLabel(s.as_of) : '—'}</td>
                      <td className="right num">
                        {s.declared_value ? money(Number(s.declared_value)) : '—'}
                      </td>
                      <td className="right num">
                        {s.computed_value ? money(Number(s.computed_value)) : '—'}
                      </td>
                      <td>
                        <Chip tone={s.recon_status === 'passed' ? 'pos'
                          : s.recon_status === 'failed' ? 'neg' : ''}>
                          {s.recon_status === 'passed' ? 'adds up'
                            : s.recon_status === 'failed' ? 'does not add up'
                              : 'no total to check'}
                        </Chip>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
