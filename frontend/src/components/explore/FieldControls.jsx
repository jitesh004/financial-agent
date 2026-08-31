import React from 'react';

/* Field/operator/value controls, shared by the widget editor and the
   dashboard's own filter bar.
 *
 * Both build the same kind of filter and must offer exactly the same
 * operators and value pickers - a filter that behaves differently depending on
 * where it was typed is a bug waiting to be reported as "the numbers don't
 * match". */

export function groupOptions(items) {
  const byGroup = new Map();
  items.forEach((item) => {
    if (!byGroup.has(item.group)) byGroup.set(item.group, []);
    byGroup.get(item.group).push(item);
  });
  return [...byGroup.entries()];
}

export function FieldSelect({ fields, value, onChange, className = 'xp-select' }) {
  return (
    <select className={className} value={value} onChange={(e) => onChange(e.target.value)}>
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

/* A value editor that follows the field it belongs to: a picker where the
   server could enumerate the values, a pair of boxes for a range, nothing at
   all for a yes/no. Nobody should have to type an account id by hand. */
export function FilterValue({ field, filter, options, onChange }) {
  const arity = valueArity(filter.op);
  if (arity === 'none') return null;

  const choices = field.options ? (options[field.options] || []) : null;

  if (arity === 'many' && choices) {
    const selected = Array.isArray(filter.value) ? filter.value : [];
    const toggle = (value) => onChange(
      selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value],
    );
    return (
      <div className="xp-options">
        {choices.length === 0 && <div className="xp-hint">Nothing to choose from yet.</div>}
        {choices.map((choice) => (
          <label className="xp-option" key={choice.value}>
            <input type="checkbox" checked={selected.includes(choice.value)}
              onChange={() => toggle(choice.value)} />
            {choice.label}
          </label>
        ))}
      </div>
    );
  }

  if (arity === 'many') {
    // No enumerable options: take a comma-separated list rather than
    // pretending the operator is unavailable.
    return (
      <input className="xp-input slim" placeholder="value, value, value"
        value={(Array.isArray(filter.value) ? filter.value : []).join(', ')}
        onChange={(e) => onChange(
          e.target.value.split(',').map((v) => v.trim()).filter(Boolean),
        )} />
    );
  }

  const numeric = field.type === 'money' || field.type === 'number';

  if (arity === 'two') {
    const pair = Array.isArray(filter.value) ? filter.value : ['', ''];
    return (
      <div className="xp-row">
        <input className="xp-input slim" type={numeric ? 'number' : 'text'}
          placeholder="from" value={pair[0] ?? ''}
          onChange={(e) => onChange([e.target.value, pair[1] ?? ''])} />
        <input className="xp-input slim" type={numeric ? 'number' : 'text'}
          placeholder="to" value={pair[1] ?? ''}
          onChange={(e) => onChange([pair[0] ?? '', e.target.value])} />
      </div>
    );
  }

  if (choices) {
    return (
      <select className="xp-select slim" value={filter.value ?? ''}
        onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose…</option>
        {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
    );
  }

  return (
    <input className="xp-input slim" type={numeric ? 'number' : 'text'}
      value={filter.value ?? ''}
      placeholder={field.type === 'money' ? 'amount in rupees' : 'value'}
      onChange={(e) => onChange(e.target.value)} />
  );
}

/* Changing the field or the operator can strand a value that no longer makes
   sense - a list of categories left behind after switching to "more than", or
   a single string where the operator now wants two. Both are reset here so a
   half-converted filter can never reach the server. */
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
  const field = fieldMap[filter.field];
  if (!field) return null;
  return (
    <div className="xp-item">
      <div className="xp-row">
        <FieldSelect fields={fields} value={filter.field} className="xp-select slim"
          onChange={(key) => onChange(reconcileFilter(filter, { field: key }, fieldMap))} />
        <select className="xp-select slim" style={{ maxWidth: 140 }} value={filter.op}
          onChange={(e) => onChange(reconcileFilter(filter, { op: e.target.value }, fieldMap))}>
          {field.ops.map((op) => (
            <option key={op} value={op}>{opLabels[op] || op}</option>
          ))}
        </select>
        <button className="xp-icon-btn" title="Remove this filter" onClick={onRemove}>✕</button>
      </div>
      <FilterValue field={field} filter={filter} options={options}
        onChange={(value) => onChange({ ...filter, value })} />
      {field.hint && <div className="xp-hint">{field.hint}</div>}
    </div>
  );
}
