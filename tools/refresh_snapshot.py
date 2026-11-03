"""Bundle the base database for the hosted console.

Streamlit Community Cloud restarts the app often. The console restores this snapshot on a
cold start instead of re-running the 90-day simulation while a visitor waits.
Run:  python run_pipeline.py  then  python tools/refresh_snapshot.py
"""
import gzip
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "hub.db"
DST = ROOT / "app" / "snapshot"

conn = sqlite3.connect(str(DB))
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # fold the write-ahead log into the main file
conn.close()
DST.mkdir(parents=True, exist_ok=True)
with open(DB, "rb") as src, gzip.open(DST / "hub.db.gz", "wb", compresslevel=9) as dst:
    shutil.copyfileobj(src, dst)
shutil.copy(DB.parent / "run_summary.json", DST / "run_summary.json")
print("wrote", DST / "hub.db.gz")
