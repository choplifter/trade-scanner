import { useCallback, useEffect, useMemo, useState } from "react";

import { useScreener } from "../../hooks/useScreener";
import type { ScannerRow } from "../../types/alpaca";
import type { FieldSpec, Operator, Preset, Screen, ScreenFilter } from "../../types/screener";
import { formatDollarVolume, formatPct, formatPrice, formatRvol, formatShares } from "../../utils/format";

interface Props {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

/** Human wording for each operator. Keyed by the backend's operator names,
 * so an operator the server stops offering simply disappears from the UI. */
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

/** Starting columns. Not a limit -- the column picker offers the whole
 * registry, and this list only decides what's shown before you choose. */
const DEFAULT_COLUMNS = ["symbol", "last_price", "pct_change", "rvol", "dollar_volume_today", "float_shares"];

const BOOLEAN_OPS: Operator[] = ["is_true", "is_false"];

function needsValue(op: Operator): boolean {
  return !BOOLEAN_OPS.includes(op);
}

/** Formats by the field's declared type rather than by field name, so a new
 * numeric field added on the backend formats correctly here with no change. */
function formatCell(value: unknown, spec: FieldSpec | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  if (!spec) return String(value);
  if (spec.type === "boolean") return value ? "Yes" : "No";
  if (spec.type === "text") return String(value);
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  if (spec.type === "percent") return formatPct(num);
  if (spec.type === "currency") {
    return Math.abs(num) >= 1_000_000 ? formatDollarVolume(num) : formatPrice(num);
  }
  if (spec.name === "rvol") return formatRvol(num);
  return Math.abs(num) >= 1_000 ? formatShares(num) : num.toFixed(2);
}

/** Filter values are typed as text. Numeric operators need numbers, "is one
 * of" needs a list, text operators keep the string. Anything unparseable is
 * passed through and the backend's comparison drops it -- the UI never
 * blocks a keystroke mid-edit. */
function coerce(raw: string, op: Operator): string | number | string[] | null {
  if (raw === "") return null;
  if (op === "eq" || op === "ne" || op === "contains") return raw;
  if (op === "in") return raw.split(",").map((part) => part.trim()).filter(Boolean);
  return coerceBound(raw);
}

/** The second bound of a "between". Always numeric -- no operator that uses
 * value2 is a text or list operator -- so this stays narrower than coerce()
 * and keeps the list case out of value2's type. */
function coerceBound(raw: string): string | number | null {
  if (raw === "") return null;
  const num = Number(raw);
  return Number.isFinite(num) ? num : raw;
}

interface DraftFilter {
  field: string;
  op: Operator;
  value: string;
  value2: string;
}

export function ScreenerWidget({ selectedSymbol, onSelectSymbol }: Props) {
  const { fields, presets, result, loading, error, run } = useScreener();
  const [filters, setFilters] = useState<DraftFilter[]>([]);
  const [columns, setColumns] = useState<string[]>(DEFAULT_COLUMNS);
  const [sortBy, setSortBy] = useState("pct_change");
  const [descending, setDescending] = useState(true);
  const [limit, setLimit] = useState(100);
  const [showColumns, setShowColumns] = useState(false);

  const fieldsByName = useMemo(
    () => new Map(fields.map((f) => [f.name, f])),
    [fields],
  );
  const filterableFields = useMemo(() => fields.filter((f) => f.operators.length > 0), [fields]);

  const buildScreen = useCallback(
    (): Screen => ({
      filters: filters.map(
        (f): ScreenFilter => ({
          field: f.field,
          op: f.op,
          value: needsValue(f.op) ? coerce(f.value, f.op) : null,
          value2: f.op === "between" ? coerceBound(f.value2) : null,
        }),
      ),
      sort_by: sortBy,
      descending,
      limit,
    }),
    [filters, sortBy, descending, limit],
  );

  // Run once as soon as the registry lands, so the widget shows results
  // instead of an empty table you have to press a button to fill.
  useEffect(() => {
    if (fields.length > 0 && result === null && !loading && error === null) {
      run(buildScreen());
    }
    // Only on registry arrival -- buildScreen changes on every edit, and
    // depending on it here would re-run the screen while the user is typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fields.length]);

  const addFilter = () => {
    const first = filterableFields[0];
    if (!first) return;
    setFilters((prev) => [...prev, { field: first.name, op: first.operators[0], value: "", value2: "" }]);
  };

  const updateFilter = (index: number, patch: Partial<DraftFilter>) => {
    setFilters((prev) =>
      prev.map((entry, i) => {
        if (i !== index) return entry;
        const next = { ...entry, ...patch };
        // Changing the field can invalidate the operator (a boolean field
        // has no "greater than"), so fall back to that field's first one
        // rather than sending the server an operator it will reject.
        const spec = fieldsByName.get(next.field);
        if (spec && !spec.operators.includes(next.op)) {
          next.op = spec.operators[0];
        }
        return next;
      }),
    );
  };

  const loadPreset = (preset: Preset) => {
    setFilters(
      preset.screen.filters.map((f) => ({
        field: f.field,
        op: f.op,
        value: f.value === null || f.value === undefined ? "" : String(f.value),
        value2: f.value2 === null || f.value2 === undefined ? "" : String(f.value2),
      })),
    );
    // A preset carries its sort too -- loading "Top Losers" and keeping a
    // descending sort would silently invert it.
    setSortBy(preset.screen.sort_by);
    setDescending(preset.screen.descending);
    setLimit(preset.screen.limit);
  };

  const cellValue = (row: ScannerRow, name: string): unknown => {
    const spec = fieldsByName.get(name);
    if (spec?.derived) return result?.derived?.[name]?.[row.symbol] ?? null;
    return (row as unknown as Record<string, unknown>)[name];
  };

  return (
    <section className="widget screener-widget">
      <header className="widget-header">
        <h2>Screener</h2>
        <div className="screener-actions">
          <select
            className="screener-preset"
            value=""
            onChange={(e) => {
              const preset = presets.find((p) => p.name === e.target.value);
              if (preset) loadPreset(preset);
            }}
          >
            <option value="">Load preset…</option>
            {presets.map((p) => (
              <option key={p.name} value={p.name} title={p.description}>
                {p.label}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => setShowColumns((v) => !v)}>
            Columns
          </button>
          <button type="button" onClick={() => run(buildScreen())} disabled={loading || fields.length === 0}>
            {loading ? "Running…" : "Run"}
          </button>
        </div>
      </header>

      {error && <p className="widget-error">{error}</p>}

      {showColumns && (
        <div className="screener-columns">
          {fields.map((f) => (
            <label key={f.name}>
              <input
                type="checkbox"
                checked={columns.includes(f.name)}
                onChange={(e) =>
                  setColumns((prev) =>
                    e.target.checked ? [...prev, f.name] : prev.filter((c) => c !== f.name),
                  )
                }
              />
              {f.label}
            </label>
          ))}
        </div>
      )}

      <div className="screener-filters">
        {filters.map((filter, index) => {
          const spec = fieldsByName.get(filter.field);
          return (
            <div className="screener-filter-row" key={index}>
              <select value={filter.field} onChange={(e) => updateFilter(index, { field: e.target.value })}>
                {filterableFields.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.label}
                  </option>
                ))}
              </select>
              <select
                value={filter.op}
                onChange={(e) => updateFilter(index, { op: e.target.value as Operator })}
              >
                {(spec?.operators ?? []).map((op) => (
                  <option key={op} value={op}>
                    {OPERATOR_LABELS[op]}
                  </option>
                ))}
              </select>
              {needsValue(filter.op) && (
                <input
                  type="text"
                  value={filter.value}
                  placeholder="value"
                  onChange={(e) => updateFilter(index, { value: e.target.value })}
                />
              )}
              {filter.op === "between" && (
                <input
                  type="text"
                  value={filter.value2}
                  placeholder="and"
                  onChange={(e) => updateFilter(index, { value2: e.target.value })}
                />
              )}
              <button
                type="button"
                title="Remove this filter"
                onClick={() => setFilters((prev) => prev.filter((_, i) => i !== index))}
              >
                ✕
              </button>
            </div>
          );
        })}
        <div className="screener-filter-actions">
          <button type="button" onClick={addFilter} disabled={filterableFields.length === 0}>
            + Add filter
          </button>
          <label>
            Sort
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
              {fields.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => setDescending((v) => !v)}>
            {descending ? "High → Low" : "Low → High"}
          </button>
          <label>
            Limit
            <input
              type="number"
              min={1}
              step={10}
              value={limit}
              onChange={(e) => setLimit(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
        </div>
      </div>

      {result && (
        <p className="screener-summary">
          {result.total_matched} of {result.tradable_size} tradable matched ({result.universe_size} in
          universe); showing {result.rows.length}
          {result.is_latest_session ? " — last completed session" : ""}
        </p>
      )}

      <div className="screener-table-wrap">
        <table className="scanner-table">
          <thead>
            <tr>
              {columns.map((name) => (
                <th key={name}>{fieldsByName.get(name)?.label ?? name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(result?.rows ?? []).map((row) => (
              <tr
                key={row.symbol}
                onClick={() => onSelectSymbol(row.symbol)}
                className={row.symbol === selectedSymbol ? "selected" : undefined}
              >
                {columns.map((name) => {
                  const value = cellValue(row, name);
                  const isChange = name === "pct_change";
                  return (
                    <td
                      key={name}
                      className={isChange ? (row.pct_change >= 0 ? "delta-up" : "delta-down") : undefined}
                    >
                      {formatCell(value, fieldsByName.get(name))}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {result && result.rows.length === 0 && <p className="screener-summary">No matches.</p>}
      </div>
    </section>
  );
}
