"""Project paths are relative to the repository, never the current shell directory."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(name.strip(), value)

def project_config() -> dict:
    return read_json(ROOT / "config" / "project.json")

def source_registry() -> list[dict]:
    return read_json(ROOT / "config" / "sources.json")["sources"]
