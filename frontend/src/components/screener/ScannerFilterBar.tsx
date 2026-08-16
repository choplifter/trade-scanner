import type { FieldSpec, Operator, Screen, ScreenFilter } from "../../types/screener";

interface Props {
  fields: FieldSpec[];
  screen: Screen;
  onChange: (screen: Screen) => void;
}

/** Human wording per operator, keyed by the backend's own operator names --
 * an operator the server stops offering simply disappears from the UI. */
const OPERATOR_LABELS: Record<Operator, string> = {
  gt: "greater than",
  gte: "at least",
  lt: "less than",
  lte: "at most",
  between: "between",
  eq: "is",
  ne: "is not",
  contains: "contains",
  in: "is one of",
  is_true: "is true",
  is_false: "is false",
};

const BOOLEAN_OPS: Operator[] = ["is_true", "is_false"];

function needsValue(op: Operator): boolean {
  return !BOOLEAN_OPS.includes(op);
}

/** Filter values are typed as text. Numeric operators want numbers, "is one
 * of" wants a list, text operators keep the string. Anything unparseable is
 * passed through and the backend's comparison drops it -- the UI never blocks
 * a keystroke mid-edit. */
function coerce(raw: string, op: Operator): string | number | string[] | null {
  if (raw === "") return null;
  if (op === "eq" || op === "ne" || op === "contains") return raw;
  if (op === "in") return raw.split(",").map((part) => part.trim()).filter(Boolean);
  const num = Number(raw);
  return Number.isFinite(num) ? num : raw;
}

function display(value: ScreenFilter["value"]): string {
  if (value === null || value === undefined) return "";
  return Array.isArray(value) ? value.join(", ") : String(value);
}

/**
 * The filter builder for the unified scanner widget. Entirely generated from
 * the server's field registry -- which fields exist, what type each is, and
 * which operators apply to that type all come from `fields`, so a field added
 * on the backend appears here with no change to this file.
 */
export function ScannerFilterBar({ fields, screen, onChange }: Props) {
  const fieldsByName = new Map(fields.map((f) => [f.name, f]));
  const filterable = fields.filter((f) => f.operators.length > 0);

  const patch = (index: number, next: Partial<ScreenFilter>) => {
    const filters = screen.filters.map((entry, i) => {
      if (i !== index) return entry;
      const merged = { ...entry, ...next };
      // Changing the field can invalidate the operator -- a boolean field has
      // no "greater than" -- so fall back to that field's first operator
      // rather than sending the server one it will reject.
      const spec = fieldsByName.get(merged.field);
      if (spec && !spec.operators.includes(merged.op)) {
        merged.op = spec.operators[0];
      }
      return merged;
    });
    onChange({ ...screen, filters });
  };

  const addFilter = () => {
    const first = filterable[0];
    if (!first) return;
    onChange({ ...screen, filters: [...screen.filters, { field: first.name, op: first.operators[0], value: null }] });
  };

  return (
    <div className="screener-filters">
      {screen.filters.map((filter, index) => {
        const spec = fieldsByName.get(filter.field);
        return (
          <div className="screener-filter-row" key={index}>
            <select value={filter.field} onChange={(e) => patch(index, { field: e.target.value })}>
              {filterable.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.label}
                </option>
              ))}
            </select>
            <select value={filter.op} onChange={(e) => patch(index, { op: e.target.value as Operator })}>
              {(spec?.operators ?? []).map((op) => (
                <option key={op} value={op}>
                  {OPERATOR_LABELS[op]}
                </option>
              ))}
            </select>
            {needsValue(filter.op) && (
              <input
                type="text"
                placeholder="value"
                value={display(filter.value)}
                onChange={(e) => patch(index, { value: coerce(e.target.value, filter.op) })}
              />
            )}
            {filter.op === "between" && (
              <input
                type="text"
                placeholder="and"
                value={display(filter.value2)}
                onChange={(e) => patch(index, { value2: coerce(e.target.value, filter.op) as number | null })}
              />
            )}
            <button
              type="button"
              title="Remove this filter"
              onClick={() => onChange({ ...screen, filters: screen.filters.filter((_, i) => i !== index) })}
            >
              ✕
            </button>
          </div>
        );
      })}

      <div className="screener-filter-actions">
        <button type="button" onClick={addFilter} disabled={filterable.length === 0}>
          + Add filter
        </button>
        <label>
          Sort
          <select value={screen.sort_by} onChange={(e) => onChange({ ...screen, sort_by: e.target.value })}>
            {fields.map((f) => (
              <option key={f.name} value={f.name}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => onChange({ ...screen, descending: !screen.descending })}>
          {screen.descending ? "High → Low" : "Low → High"}
        </button>
        <label>
          Limit
          <input
            type="number"
            min={1}
            step={10}
            value={screen.limit}
            onChange={(e) => onChange({ ...screen, limit: Math.max(1, Number(e.target.value) || 1) })}
          />
        </label>
      </div>
    </div>
  );
}
