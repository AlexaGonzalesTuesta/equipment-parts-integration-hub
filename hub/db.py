"""SQLite schema for the simulated enterprise landscape.

Table prefixes show which system owns the data:
  erp_   Enterprise Resource Planning (master data, price list, sales orders)
  dms_   Dealer Management System (dealer inventory)
  ml_    Training data for the AI parts recommender
  hub_   The integration hub itself (inbound log, audit trail, dead letters, review queue)

The hub only touches erp_ and dms_ tables through the connector classes in
connectors.py, the same way it would call real system APIs.
"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_dealers (
    dealer_id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL, country TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS erp_customers (
    customer_id TEXT PRIMARY KEY, name TEXT NOT NULL, industry TEXT NOT NULL, region TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS erp_parts (
    part_number TEXT PRIMARY KEY, description TEXT NOT NULL, category TEXT NOT NULL,
    list_price REAL NOT NULL CHECK (list_price > 0));
CREATE TABLE IF NOT EXISTS erp_machines (
    machine_id TEXT PRIMARY KEY, model TEXT NOT NULL, machine_type TEXT NOT NULL,
    customer_id TEXT NOT NULL REFERENCES erp_customers(customer_id),
    dealer_id TEXT NOT NULL REFERENCES erp_dealers(dealer_id), in_service_year INTEGER);
CREATE TABLE IF NOT EXISTS erp_fault_catalog (
    fault_code TEXT PRIMARY KEY, system TEXT NOT NULL, description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS erp_fault_parts (
    fault_code TEXT NOT NULL REFERENCES erp_fault_catalog(fault_code),
    part_number TEXT NOT NULL REFERENCES erp_parts(part_number), quantity INTEGER NOT NULL,
    PRIMARY KEY (fault_code, part_number));
CREATE TABLE IF NOT EXISTS erp_orders (
    order_id TEXT PRIMARY KEY, channel TEXT NOT NULL, source_message_id TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL, machine_id TEXT, fault_code TEXT, dealer_id TEXT NOT NULL,
    created_at TEXT NOT NULL, status TEXT NOT NULL, fulfillment TEXT NOT NULL,
    total_value REAL NOT NULL, ai_assisted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS erp_order_lines (
    order_id TEXT NOT NULL REFERENCES erp_orders(order_id), line_no INTEGER NOT NULL,
    part_number TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price REAL NOT NULL,
    fulfilling_dealer_id TEXT, line_status TEXT NOT NULL, PRIMARY KEY (order_id, line_no));
CREATE TABLE IF NOT EXISTS dms_inventory (
    dealer_id TEXT NOT NULL, part_number TEXT NOT NULL,
    on_hand INTEGER NOT NULL CHECK (on_hand >= 0), reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    reorder_point INTEGER NOT NULL DEFAULT 0, target_stock INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (dealer_id, part_number));
CREATE TABLE IF NOT EXISTS ml_history_notes (
    note_id TEXT PRIMARY KEY, fault_code TEXT NOT NULL, note_text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hub_inbound (
    message_id TEXT PRIMARY KEY, channel TEXT NOT NULL, received_at TEXT NOT NULL,
    payload TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hub_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL, step TEXT NOT NULL,
    status TEXT NOT NULL, detail TEXT, logged_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hub_dead_letter (
    id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL, channel TEXT NOT NULL,
    reason_code TEXT NOT NULL, detail TEXT, payload TEXT NOT NULL, attempts INTEGER NOT NULL,
    replayable INTEGER NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS hub_manual_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, machine_id TEXT NOT NULL,
    predicted_fault TEXT, confidence REAL, top3 TEXT, symptom_note TEXT, created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN');
CREATE TABLE IF NOT EXISTS hub_results (
    message_id TEXT NOT NULL, channel TEXT NOT NULL, outcome TEXT NOT NULL, order_id TEXT,
    detail TEXT, latency_ms REAL NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, processed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_audit_msg ON hub_audit(message_id);
CREATE INDEX IF NOT EXISTS ix_results_msg ON hub_results(message_id);
"""


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
