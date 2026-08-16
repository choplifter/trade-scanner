import type { ScannerRow } from "./alpaca";

/** Field types the backend registry uses. Drives which operators a field
 * offers and how its values are formatted -- see app/scanners/screener.py. */
export type FieldType = "number" | "percent" | "currency" | "boolean" | "text";

export type Operator =
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "between"
  | "eq"
  | "ne"
  | "contains"
  | "in"
  | "is_true"
  | "is_false";

/** One screenable column, exactly as the server describes it. The UI builds
 * its field pickers, operator lists and column chooser from these rather
 * than hardcoding any of it -- adding a field on the backend adds it here
 * with no frontend change. */
export interface FieldSpec {
  name: string;
  label: string;
  type: FieldType;
  operators: Operator[];
  /** True for fields that aren't ScannerRow attributes (float, rank score).
   * Their values arrive in ScreenResponse.derived, keyed by symbol, rather
   * than on the row itself. */
  derived: boolean;
}

export interface ScreenFilter {
  field: string;
  op: Operator;
  value?: string | number | string[] | null;
  value2?: string | number | null;
}

export interface Screen {
  filters: ScreenFilter[];
  sort_by: string;
  descending: boolean;
  limit: number;
}

export interface Preset {
  name: string;
  label: string;
  description: string;
  screen: Screen;
}

export interface ScreenResponse {
  session: string;
  is_latest_session: boolean;
  /** Matches before `limit` truncated them -- lets the UI say "showing 100
   * of 412" rather than leaving a full page ambiguous. */
  total_matched: number;
  tradable_size: number;
  universe_size: number;
  rows: ScannerRow[];
  /** { field_name: { symbol: value } } for derived fields only. */
  derived: Record<string, Record<string, number | null>>;
}

export interface FieldsResponse {
  fields: FieldSpec[];
}

export interface PresetsResponse {
  presets: Preset[];
}
