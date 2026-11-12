"""Regenerate MANIFEST.sha256.json: size and SHA-256 of every file in the release.

Run from any directory: python code/write_manifest.py
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"MANIFEST.sha256.json"}
entries = {}
for f in sorted(ROOT.rglob("*")):
    rel = f.relative_to(ROOT).as_posix()
    if f.is_file() and rel not in SKIP and not rel.startswith(".git/") and "__pycache__" not in rel:
        entries[rel] = {"bytes": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
(ROOT / "MANIFEST.sha256.json").write_text(json.dumps(entries, indent=2) + "\n")
print(f"wrote MANIFEST.sha256.json with {len(entries)} files")
