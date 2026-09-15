"""Read-only SQLite access to a published snapshot, shared by every stage.

Deliberately kept out of any build module: serving a snapshot must not import
the spatial build dependencies (shapely/pyproj) that only Stage1B construction
needs. Opening a database read-only is a write guard, NOT evidence that its
contents were reviewed; snapshot build_id and pointer checks stay with each
caller.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def ro_connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db
