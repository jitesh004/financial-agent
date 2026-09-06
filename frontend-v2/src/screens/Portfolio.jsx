/* What you own, read off the holdings statements rather than off a price feed.
 *
 * Every figure here is units × NAV as printed on a statement that reconciled
 * against its own declared total. No live prices: this app has no way to
 * verify one, and a net worth that changes when you reload it cannot be
 * checked against anything later.
 */

import React, { useMemo } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { colorFor, compact, dateLabel, money, titleCase } from '../core/format';
import {
  BarList, Button, Callout, Card, Chip, Empty, Legend, Loading, Section, SortHeader,
  Stat, Table, useSorted,
} from '../ui';
import { Donut } from '../ui/charts';

const KIND = {
  equity: 'Equity', mutual_fund: 'Mutual funds', etf: 'ETFs',
  bond: 'Bonds & debt', nps: 'NPS', epf: 'EPF', other: 'Other',
};

export default function Portfolio({ onImport }) {
  const { data, loading, error } = useQuery('portfolio', () => api.portfolio());
  const holdings = data?.holdings || [];
  const { sorted, sort, by } = useSorted(holdings, { key: 'value', dir: 'desc' });

  const byKind = useMemo(() => (data?.by_kind || []).map((b, i) => ({
    label: KIND[b.kind] || titleCase(b.kind),
    value: Number(b.value) || 0,
    color: colorFor(i),
  })), [data]);

  if (error) return <Callout tone="warn">{error.message}</Callout>;
  if (loading) return <Loading label="Reading your holdings…" />;

  if (!holdings.length) {
    return (
      <Empty title="No holdings imported yet" icon="briefcase"
        action={onImport && (
          <Button variant="primary" icon="upload" onClick={onImport}>Import statements</Button>
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
        <Callout tone="warn">
          <strong>
            {data.unreconciled.length} statement
            {data.unreconciled.length === 1 ? '' : 's'} did not add up.
          </strong>{' '}
          The holdings read out of {data.unreconciled.map((s) => s.filename).join(', ')} do
          not total the value the document declares, so the figures below may be
          incomplete.
          <div className="small muted" style={{ marginTop: 6 }}>
            {data.unreconciled[0].message}
          </div>
        </Callout>
      )}

      <div className="grid cols-4">
        <Stat label="Portfolio value" value={Number(totals.value) || 0} tone="accent" />
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
                  + `${totals.uncosted_instruments === 1 ? '' : 's'} with no cost` : '')}
        />
        <Stat
          label="Instruments"
          value={String(totals.instruments ?? holdings.length)}
          note={totals.as_of ? `valued ${dateLabel(totals.as_of)}` : ''}
        />
      </div>

      <div className="split">
        <Card title="By asset type">
          <Donut data={byKind} height={250} centerLabel="portfolio" />
          <Legend items={byKind} />
        </Card>

        <Card title="Largest holdings" sub={`${holdings.length} in total`}>
          <BarList
            items={holdings.slice(0, 12).map((h, i) => ({
              label: h.instrument || h.symbol || h.isin,
              value: Number(h.value) || 0,
              color: colorFor(i),
            }))}
            total={Number(totals.value) || 0}
          />
        </Card>
      </div>

      <Section title="Holdings" note={`${holdings.length} positions`} />
      <Card pad={false}>
        <Table scrollY maxHeight={560}>
          <thead>
            <tr>
              <SortHeader label="Instrument" field="instrument" sort={sort} onSort={by} />
              <th>Type</th>
              <SortHeader label="Units" field="units" sort={sort} onSort={by} align="right" />
              <SortHeader label="NAV / price" field="nav" sort={sort} onSort={by} align="right" />
              <SortHeader label="Value" field="value" sort={sort} onSort={by} align="right" />
              <th className="right">Gain</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((h) => {
              const value = Number(h.value) || 0;
              const cost = Number(h.invested) || (Number(h.units) || 0) * (Number(h.avg_cost) || 0);
              const delta = cost ? value - cost : null;
              return (
                <tr key={h.id}>
                  <td>
                    <div className="truncate" style={{ maxWidth: 300 }} title={h.instrument}>
                      {h.instrument || h.symbol || h.isin}
                    </div>
                    <div className="tiny dim">
                      {h.isin}{h.folio ? ` · folio ${h.folio}` : ''}
                    </div>
                  </td>
                  <td><Chip>{KIND[h.kind] || h.kind}</Chip></td>
                  <td className="right num">
                    {h.units
                      ? Number(h.units).toLocaleString('en-IN', { maximumFractionDigits: 3 })
                      : '—'}
                  </td>
                  <td className="right num">{h.nav ? money(Number(h.nav), true) : '—'}</td>
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
        </Table>
      </Card>

      {data.statements?.length > 0 && (
        <>
          <Section title="Where these came from" />
          <Card pad={false}>
            <Table>
              <thead>
                <tr>
                  <th>File</th><th>Provider</th><th>Valued</th>
                  <th className="right">Declared</th><th className="right">Computed</th>
                  <th>Checks out?</th>
                </tr>
              </thead>
              <tbody>
                {data.statements.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <div className="truncate" style={{ maxWidth: 240 }}>{s.source_filename}</div>
                    </td>
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
                          : s.recon_status === 'failed' ? 'does not add up' : 'no total to check'}
                      </Chip>
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </>
      )}
    </>
  );
}
