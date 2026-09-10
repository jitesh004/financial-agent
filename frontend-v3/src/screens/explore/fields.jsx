/* Field / operator / value controls, shared by the widget editor and the
 * board's own filter bar.
 */

import React from 'react';
import { IconButton } from '../../ui';

export function groupOptions(items) {
  const byGroup = new Map();
  items.forEach((item) => {
    if (!byGroup.has(item.group)) byGroup.set(item.group, []);
    byGroup.get(item.group).push(item);
  });
  return [...byGroup.entries()];
}

export function FieldSelect({ fields, value, onChange, style }) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)} style={style}>
      {groupOptions(fields).map(([group, items]) => (
        <optgroup label={group} key={group}>
          {items.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </optgroup>
      ))}
    </select>
  );
}

export function valueArity(op) {
  if (['is_true', 'is_false', 'is_empty', 'is_not_empty'].includes(op)) return 'none';
  if (['in', 'not_in'].includes(op)) return 'many';
  if (op === 'between') return 'two';
  return 'one';
}

export function FilterValue({ field, filter, options, onChange }) {
  const arity = valueArity(filter.op);
  if (arity === 'none') return null;
  const choices = field?.options && options ? (options[field.options] || []) : null;

  if (arity === 'many' && choices) {
    const selected = Array.isArray(filter.value) ? filter.value : [];
    const toggle = (v) => onChange(
      selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]
    );
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 180, overflowY: 'auto',
        padding: 8, border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-sm)',
        background: 'var(--surface-2)',
      }}>
        {!choices.length && <div className="tiny muted">Nothing to choose from yet.</div>}
        {choices.map((c) => (
          <label key={c.value} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={selected.includes(c.value)}
              onChange={() => toggle(c.value)}
              style={{ accentColor: 'var(--brand-primary)' }}
            />
            {c.label}
          </label>
        ))}
      </div>
    );
  }

  if (arity === 'many') {
    return (
      <input
        className="input"
        placeholder="value, value, value"
        value={(Array.isArray(filter.value) ? filter.value : []).join(', ')}
        onChange={(e) => onChange(e.target.value.split(',').map((v) => v.trim()).filter(Boolean))}
      />
    );
  }

  const numeric = field.type === 'money' || field.type === 'number';

  if (arity === 'two') {
    const pair = Array.isArray(filter.value) ? filter.value : ['', ''];
    return (
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          className="input"
          type={numeric ? 'number' : 'text'}
          placeholder="From"
          value={pair[0] ?? ''}
          onChange={(e) => onChange([e.target.value, pair[1] ?? ''])}
          style={{ flex: 1 }}
        />
        <input
          className="input"
          type={numeric ? 'number' : 'text'}
          placeholder="To"
          value={pair[1] ?? ''}
          onChange={(e) => onChange([pair[0] ?? '', e.target.value])}
          style={{ flex: 1 }}
        />
      </div>
    );
  }

  if (choices) {
    return (
      <select className="select" value={filter.value ?? ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose…</option>
        {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
    );
  }

  return (
    <input
      className="input"
      type={numeric ? 'number' : 'text'}
      value={filter.value ?? ''}
      placeholder={field.type === 'money' ? 'Amount in rupees' : 'Value'}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function reconcileFilter(filter, patch, fieldMap) {
  const next = { ...filter, ...patch };
  if (patch.field) {
    const allowed = fieldMap[patch.field]?.ops || ['in'];
    if (!allowed.includes(next.op)) [next.op] = allowed;
    next.value = valueArity(next.op) === 'many' ? [] : '';
  }
  if (patch.op && valueArity(patch.op) !== valueArity(filter.op)) {
    next.value = ['many', 'two'].includes(valueArity(patch.op)) ? [] : '';
  }
  return next;
}

export function FilterRow({ filter, fields, fieldMap, opLabels, options, onChange, onRemove }) {
  const field = fieldMap?.[filter.field];
  if (!field) return null;
  return (
    <div style={{
      padding: 'var(--space-3)',
      display: 'grid',
      gap: 8,
      background: 'var(--surface-2)',
      borderRadius: 'var(--radius-md)',
      border: '1px solid var(--border-subtle)',
    }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <FieldSelect
          fields={fields}
          value={filter.field}
          style={{ flex: 1 }}
          onChange={(key) => onChange(reconcileFilter(filter, { field: key }, fieldMap))}
        />
        <select
          className="select"
          value={filter.op}
          style={{ maxWidth: 140 }}
          onChange={(e) => onChange(reconcileFilter(filter, { op: e.target.value }, fieldMap))}
        >
          {(field.ops || []).map((op) => (
            <option key={op} value={op}>{opLabels?.[op] || op}</option>
          ))}
        </select>
        <IconButton
          icon="x"
          label="Remove filter"
          size="sm"
          onClick={onRemove}
        />
      </div>
      <FilterValue
        field={field}
        filter={filter}
        options={options}
        onChange={(value) => onChange({ ...filter, value })}
      />
      {field.hint && <div className="tiny muted">{field.hint}</div>}
    </div>
  );
}
