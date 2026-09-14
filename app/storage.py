"""Idempotent local schema creation; no user data is populated at Stage 0."""
import sqlite3
from pathlib import Path
from app.config import ROOT

DB_PATH = ROOT / "data" / "local" / "playmap.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta(version INTEGER NOT NULL);
INSERT INTO schema_meta(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);
CREATE TABLE IF NOT EXISTS places(
  place_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  latitude REAL,
  longitude REAL,
  verification_status TEXT NOT NULL DEFAULT 'unverified',
  document_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_places_coordinates ON places(latitude, longitude);
CREATE TABLE IF NOT EXISTS place_sources(
  place_id TEXT NOT NULL REFERENCES places(place_id),
  source_key TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  PRIMARY KEY(place_id, source_key, source_record_id)
);
CREATE TABLE IF NOT EXISTS entrances(
  entrance_id TEXT PRIMARY KEY,
  place_id TEXT NOT NULL REFERENCES places(place_id),
  document_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visit_options(
  option_id TEXT PRIMARY KEY,
  place_id TEXT NOT NULL REFERENCES places(place_id),
  document_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL DEFAULT 0,
  document_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans(
  plan_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(session_id),
  state_revision INTEGER NOT NULL,
  document_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

def initialize_database(path: Path = DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(SCHEMA)
    return path

def place_count():
    if not DB_PATH.exists():
        return 0
    with sqlite3.connect(DB_PATH) as db:
        return db.execute("SELECT COUNT(*) FROM places").fetchone()[0]
