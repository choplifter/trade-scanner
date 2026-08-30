/** Seeds a fresh browser's watchlist (see useWatchlist) with the symbols
 * from the repo-root symbols_pinned.txt -- a manually-curated list that
 * predates this panel. Copied in once rather than fetched at runtime: once
 * localStorage holds a list, this constant is never consulted again, so
 * there's no reason to round-trip it through the backend on every load. */
export const DEFAULT_WATCHLIST_SYMBOLS: string[] = [
  "CRWV", "SMCI", "BAC", "WETO", "KO", "NU", "SOFI", "PATH", "WFC", "AAL",
  "PFE", "VZ", "BSX", "SLV", "ASTS", "HPE", "ONDS", "AXTI", "BMNR", "MCHP",
  "BMY", "CDE", "CVS", "CVNA", "ON", "WBD", "CSX", "MO", "SLB", "ORLY",
  "FIG", "NVO", "SHEL", "MARA", "GM", "MDLZ", "CTSH", "STM", "PDD", "APLD",
  "DVN", "BKR", "HIMS", "B", "DXCM", "BYND", "IAU", "CCL", "SO", "WMB",
  "DAL", "HBAN", "PCG", "FAST", "IBKR", "HPQ", "ECHO", "MP", "HAL", "HUT",
  "CARR", "KVUE", "CELH", "OKLO", "KR", "EQT", "GEHC", "CTVA", "MET", "GLDM",
  "MNST", "BP", "GIS", "SXTC", "TTD", "DKNG", "O", "FITB", "VTR", "ASX",
  "ADM", "DFNS", "DOW", "CNC", "LYB", "GPN", "PBR", "DT", "SMR", "CCI",
  "DINO", "AA", "AMKR", "BBY", "VMRK", "SYF", "CAVA", "CFG", "PPL", "OTIS",
];
