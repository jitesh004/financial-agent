import React, { useMemo } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { colorFor, compact, dateLabel, money, titleCase } from '../core/format';
import {
  BarList, Button, Callout, Card, GlassCard, Chip, Empty, Legend, Loading, Section, SortHeader,
  Stat, Table, useSorted, Badge,
} from '../ui';
import { DonutChart } from '../ui/gauges';

const ASSET_KIND = {
  equity: 'Equity Shares',
  mutual_fund: 'Mutual Funds',
  etf: 'Exchange Traded Funds',
  bond: 'Bonds & Fixed Income',
  nps: 'National Pension System',
  epf: 'Employees Provident Fund',
  other: 'Alternative Assets',
};

export default function Portfolio({ onImport }) {
  const { data, loading, error } = useQuery('portfolio', () => api.portfolio());
  const holdings = data?.holdings || [];
  const { sorted, sort, by } = useSorted(holdings, { key: 'value', dir: 'desc' });

  const byKind = useMemo(() => (data?.by_kind || []).map((b, i) => ({
    label: ASSET_KIND[b.kind] || titleCase(b.kind),
    value: Number(b.value) || 0,
    color: colorFor(i),
  })), [data]);

  if (error) return <Callout tone="warn">{error.message}</Callout>;
  if (loading) return <Loading message="Reconciling holdings statements and printed NAVs…" />;

  if (!holdings.length) {
    return (
      <Empty
        title="No investment holdings statements imported yet"
        icon="briefcase"
        action={onImport && (
          <Button variant="primary" icon="upload" onClick={onImport}>
            Import CAS / Holdings PDF
          </Button>
        )}
      >
        Import Consolidated Account Statements (CAS) from CAMS/KFintech, CDSL/NSDL demat holdings, or broker portfolio PDFs.
        Every asset is valued at printed document NAV and mathematically cross-verified against declared statement totals.
      </Empty>
    );
  }

  const totals = data.totals || {};
  const gain = totals.gain != null ? Number(totals.gain) : null;
  const invested = Number(totals.invested) || 0;
  const portfolioVal = Number(totals.value) || 0;

  return (
    <div className="portfolio-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Asset Holdings & Portfolio</h1>
            <Badge tone="brand" size="sm">
              Statement-Printed NAVs
            </Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Strict verification: units × printed NAV as declared on audited statements. Zero synthetic price feeds.
          </p>
        </div>
      </div>

      {data.unreconciled?.length > 0 && (
        <Callout tone="warn">
          <strong>{data.unreconciled.length} statement{data.unreconciled.length === 1 ? '' : 's'} did not mathematically tie out.</strong>
          {' '}Computed line-item holdings do not sum to declared statement total: {data.unreconciled.map((s) => s.filename).join(', ')}.
        </Callout>
      )}

      {/* Top Headline Stats */}
      <div className="stats-grid">
        <Stat
          label="Total Portfolio Valuation"
          value={money(portfolioVal)}
          tone="brand"
          sub={totals.as_of ? `Valued as of ${dateLabel(totals.as_of)}` : 'Aggregated positions'}
        />
        <Stat
          label="Cumulative Invested Capital"
          value={invested > 0 ? money(invested) : '—'}
          sub="Reported acquisition cost basis"
        />
        <Stat
          label="Unrealized Return"
          value={gain == null ? '—' : money(gain)}
          tone={gain == null ? undefined : gain >= 0 ? 'pos' : 'neg'}
          /* The coverage belongs NEXT TO the percentage, not implied by the
             words "documented cost". A 99.9% return sitting beside a 19.88
             lakh valuation reads as "my portfolio doubled" when it is a
             gain on ten instruments out of fifty - 5% of the value. The
             number is right; on its own it says the wrong thing. */
          sub={gain == null || !(invested > 0)
            ? 'No cost basis declared on demat statements'
            : `${((gain / invested) * 100).toFixed(1)}% on ${money(invested)} of cost basis`
              + ` — ${totals.costed_instruments ?? 0} of ${totals.instruments ?? 0}`
              + ` holdings${portfolioVal > 0
                  ? `, ${((Number(totals.gain_basis_value || 0) / portfolioVal) * 100).toFixed(0)}% of value`
                  : ''}`}
        />
        <Stat
          label="Securities Tracked"
          value={String(totals.instruments ?? holdings.length)}
          sub="Distinct ISINs & folios"
        />
      </div>

      {/* Allocation Breakdown & Top Holdings */}
      <div className="grid-2">
        <GlassCard glowing title="Asset Class Allocation">
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 'var(--space-4)', padding: 'var(--space-2) 0' }}>
            <DonutChart
              data={byKind}
              size={220}
              thickness={30}
              centerLabel="Net Assets"
              centerValue={compact(portfolioVal)}
            />
            <Legend items={byKind} />
          </div>
        </GlassCard>

        <Card title="Top Position Concentrations" subtitle={`${holdings.length} distinct securities on record`}>
          <BarList
            items={holdings.slice(0, 10).map((h, i) => ({
              label: h.instrument || h.symbol || h.isin,
              value: Number(h.value) || 0,
              color: colorFor(i),
            }))}
            total={portfolioVal}
            max={10}
          />
        </Card>
      </div>

      {/* Holdings Table */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        <Section title="Constituent Securities" subtitle={`${holdings.length} reconciled holding lines`} />

        <Card pad={false}>
          <div style={{ maxHeight: 560, overflow: 'auto' }}>
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <SortHeader label="Security / Instrument" field="instrument" sort={sort} onSort={by} />
                  <th>Asset Type</th>
                  <SortHeader label="Units" field="units" sort={sort} onSort={by} align="right" />
                  <SortHeader label="Printed NAV" field="nav" sort={sort} onSort={by} align="right" />
                  <SortHeader label="Current Valuation" field="value" sort={sort} onSort={by} align="right" />
                  <th style={{ textAlign: 'right' }}>Gain / P&L</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((h) => {
                  const val = Number(h.value) || 0;
                  const cost = Number(h.invested) || (Number(h.units) || 0) * (Number(h.avg_cost) || 0);
                  const delta = cost ? val - cost : null;

                  return (
                    <tr key={h.id} className="terminal-row">
                      <td>
                        <div className="font-semibold truncate" style={{ maxWidth: 280 }} title={h.instrument}>
                          {h.instrument || h.symbol || h.isin}
                        </div>
                        <div className="tiny num muted">
                          {h.isin}{h.folio ? ` · Folio: ${h.folio}` : ''}
                        </div>
                      </td>
                      <td>
                        <Chip size="sm">{ASSET_KIND[h.kind] || h.kind}</Chip>
                      </td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>
                        {h.units ? Number(h.units).toLocaleString('en-IN', { maximumFractionDigits: 3 }) : '—'}
                      </td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>
                        {h.nav ? money(Number(h.nav), true) : '—'}
                      </td>
                      <td className="num font-semibold nowrap" style={{ textAlign: 'right' }}>
                        {money(val)}
                      </td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>
                        {delta == null ? (
                          <span className="muted">—</span>
                        ) : (
                          <span className={`font-semibold ${delta >= 0 ? 'pos' : 'neg'}`}>
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
      </div>

      {/* Statement Provenance & Tie-out Table */}
      {data.statements?.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Section
            title="Statement Provenance & Reconciliation Check"
            subtitle="Guarantees line item arithmetic matches the declared total printed by the depository or fund house."
          />

          <Card pad={false}>
            <div style={{ overflowX: 'auto' }}>
              <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th>Source Document</th>
                    <th>Depository / Registry</th>
                    <th>Valuation Date</th>
                    <th style={{ textAlign: 'right' }}>Declared Total</th>
                    <th style={{ textAlign: 'right' }}>Computed Sum</th>
                    <th>Reconciliation Audit</th>
                  </tr>
                </thead>
                <tbody>
                  {data.statements.map((s) => (
                    <tr key={s.id} className="terminal-row">
                      <td className="font-medium truncate" style={{ maxWidth: 240 }}>
                        {s.source_filename}
                      </td>
                      <td><Chip size="sm">{s.provider || s.layout}</Chip></td>
                      <td className="nowrap muted tiny">{s.as_of ? dateLabel(s.as_of) : '—'}</td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>
                        {s.declared_value ? money(Number(s.declared_value)) : '—'}
                      </td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>
                        {s.computed_value ? money(Number(s.computed_value)) : '—'}
                      </td>
                      <td>
                        <Badge
                          tone={s.recon_status === 'passed' ? 'pos' : s.recon_status === 'failed' ? 'neg' : 'brand'}
                          size="sm"
                        >
                          {s.recon_status === 'passed' ? 'Tied out (Passed)' : s.recon_status === 'failed' ? 'Mismatch (Failed)' : 'Informational'}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
