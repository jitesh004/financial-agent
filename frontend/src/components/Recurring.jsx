import React, { useState, useEffect } from 'react';
import { Card, Stat, Chip, Callout } from './ui';
import { api, money, titleCase } from '../lib';

export default function Recurring() {
  const [series, setSeries] = useState([]);
  const [loading, setLoading] = useState(true);

  function load() {
    api.request('/api/recurring').then(res => {
        setSeries(res);
        setLoading(false);
    }).catch(e => {
        console.error(e);
        setLoading(false);
    });
  }

  useEffect(() => {
      load();
  }, []);

  async function toggleActive(id, current) {
      await api.request(`/api/recurring/${id}`, {
          method: 'PATCH',
          body: JSON.stringify({ is_active: !current })
      });
      load();
  }

  if (loading) return <div className="p-4 spinner" />;
  if (!series.length) return <div className="p-4">No recurring series found.</div>;

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <h2>Recurring Series</h2>
      {series.map(s => (
          <Card key={s.id} style={{ opacity: s.is_active ? 1 : 0.6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                      <div style={{ fontWeight: 600, fontSize: 16 }}>{s.label}</div>
                      <div style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 4 }}>
                          {titleCase(s.category)} • {s.cadence_days} days • {s.occurrences} occurrences
                      </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                      <div className="num" style={{ fontWeight: 600, fontSize: 16, color: s.direction === 'credit' ? 'var(--positive)' : 'var(--text)' }}>
                          {money(parseFloat(s.median_amount))}
                      </div>
                      <button className="btn" style={{ marginTop: 8 }} onClick={() => toggleActive(s.id, s.is_active)}>
                          {s.is_active ? 'Ignore' : 'Track'}
                      </button>
                  </div>
              </div>
          </Card>
      ))}
    </div>
  );
}
