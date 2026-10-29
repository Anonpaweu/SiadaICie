CREATE TABLE IF NOT EXISTS stations (
    eva_code TEXT PRIMARY KEY,          -- np. "5100065" (kod HAFAS/EVA używany przez PKP IC)
    name TEXT NOT NULL,                 -- np. "Warszawa Centralna"
    normalized_name TEXT NOT NULL,      -- np. "warszawa centralna" (do wyszukiwania)
    metropolitan_group TEXT,            -- np. "WARSZAWA", "TROJMIASTO", "GOP", NULL dla zwykłych
    is_main_hub BOOLEAN DEFAULT 0,      -- 1 dla głównej stacji w aglomeracji (np. W-wa Centralna, Gdańsk Gł.)
    ic_id TEXT                          -- Identyfikator stacji w systemie PKP IC
);

CREATE TABLE IF NOT EXISTS cached_routes (
    train_number TEXT NOT NULL,         -- np. "IC 6502"
    travel_date TEXT NOT NULL,          -- np. "2026-11-15"
    stops_json TEXT NOT NULL,           -- Zserializowana lista obiektów StationStop
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (train_number, travel_date)
);

CREATE TABLE IF NOT EXISTS cached_seatmaps (
    cache_key TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
