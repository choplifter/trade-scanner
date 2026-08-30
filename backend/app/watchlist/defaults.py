"""A fresh user's starting watchlist -- the same list frontend/src/data/
defaultWatchlist.ts was seeded from (the repo-root symbols_pinned.txt),
copied in once rather than read from that file at runtime, matching that
file's own documented convention: "regenerating the constant from it is a
manual copy, not a build step". Now used server-side too now that watchlists
are per-user (WatchlistStore.seed_if_empty) instead of localStorage-only.
"""

DEFAULT_WATCHLIST_SYMBOLS: list[str] = [
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
]
