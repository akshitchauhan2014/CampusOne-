CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL UNIQUE,
    algorithm TEXT NOT NULL,
    training_data_size INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    dataset_kind TEXT NOT NULL CHECK(dataset_kind IN ('historical','synthetic_demo')),
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
    model_path TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_one_active ON model_runs(active) WHERE active=1;
CREATE TABLE IF NOT EXISTS risk_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    score REAL NOT NULL CHECK(score >= 0 AND score <= 1),
    band TEXT NOT NULL CHECK(band IN ('Low','Medium','High')),
    factors_json TEXT NOT NULL,
    model_version TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('model','attendance_baseline')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_risk_predictions_created ON risk_predictions(created_at);
