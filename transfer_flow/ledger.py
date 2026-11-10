"""SQLite persistence. Monetary values are integer agorot (no float arithmetic)."""
import sqlite3
from decimal import Decimal

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, balance INTEGER NOT NULL, currency TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, result TEXT);
CREATE TABLE IF NOT EXISTS accepted (reference TEXT PRIMARY KEY, batch_id TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL, amount INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, reference TEXT NOT NULL, account_id TEXT NOT NULL, delta INTEGER NOT NULL);
"""

def cents(value):
    value = Decimal(str(value))
    scaled = value * 100
    if not value.is_finite() or scaled != scaled.to_integral_value():
        raise ValueError("Amount must have at most two decimal places")
    return int(scaled)

def money(value):
    return f"{Decimal(value) / 100:.2f}"

class Ledger:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def reset(self, accounts):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for table in ("ledger", "accepted", "batches", "accounts"):
                self.db.execute(f"DELETE FROM {table}")
            for account in accounts:
                self.db.execute("INSERT INTO accounts VALUES (?, ?, ?)", (account["id"], cents(account["balance"]), account["currency"]))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def summary(self):
        current = self.db.execute("SELECT COALESCE(SUM(balance),0) FROM accounts").fetchone()[0]
        # All ledger entries must sum to zero; derive original from current minus ledger deltas.
        delta = self.db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger").fetchone()[0]
        return {"openingTotal": money(current - delta), "currentTotal": money(current), "preserved": delta == 0}

    def balances(self):
        return {r["id"]: money(r["balance"]) for r in self.db.execute("SELECT * FROM accounts ORDER BY id")}
