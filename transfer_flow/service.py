"""Deterministic business rules; transaction boundary is a complete batch."""
import json
from decimal import Decimal, InvalidOperation
from .ledger import cents

class WriteFailure(Exception):
    pass

def validate(row, db):
    ref = row.get("reference")
    if ref is not None and db.execute("SELECT 1 FROM accepted WHERE reference=?", (str(ref),)).fetchone():
        return "DUPLICATE"
    if any(row.get(k) is None or row.get(k) == "" for k in ("reference", "from", "to", "amount", "currency")):
        return "MISSING_FIELD"
    try:
        if isinstance(row["amount"], bool):
            raise InvalidOperation()
        amount = Decimal(str(row["amount"]))
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
            raise InvalidOperation()
    except (InvalidOperation, ValueError, TypeError):
        return "INVALID_AMOUNT"
    if row["currency"] != "ILS":
        return "UNSUPPORTED_CURRENCY"
    accounts = {r["id"]: r["balance"] for r in db.execute("SELECT id,balance FROM accounts")}
    if row["from"] not in accounts or row["to"] not in accounts:
        return "UNKNOWN_ACCOUNT"
    if row["from"] == row["to"]:
        return "SAME_ACCOUNT"
    if amount > 50000:
        return "EXCEEDS_LIMIT"
    if accounts[row["from"]] < cents(amount):
        return "INSUFFICIENT_FUNDS"
    return "ACCEPTED"

def process_batch(ledger, batch_id, rows, fail_on_write=None):
    db = ledger.db
    db.execute("BEGIN IMMEDIATE")
    results = []
    accepted_count = 0
    try:
        for index, row in enumerate(rows):
            outcome = validate(row, db)
            results.append({"index": index, "reference": row.get("reference"), "status": outcome if outcome in ("ACCEPTED", "DUPLICATE") else "REJECTED", "code": outcome if outcome not in ("ACCEPTED", "DUPLICATE") else None})
            if outcome != "ACCEPTED":
                continue
            accepted_count += 1
            amount = cents(row["amount"])
            source, target = row["from"], row["to"]
            db.execute("INSERT INTO accepted VALUES (?,?,?,?,?)", (str(row["reference"]), batch_id, source, target, amount))
            db.execute("UPDATE accounts SET balance=balance-? WHERE id=?", (amount, source))
            db.execute("UPDATE accounts SET balance=balance+? WHERE id=?", (amount, target))
            db.execute("INSERT INTO ledger(batch_id,reference,account_id,delta) VALUES (?,?,?,?)", (batch_id, str(row["reference"]), source, -amount))
            if accepted_count == fail_on_write:
                raise WriteFailure("Injected ledger write failure")
            db.execute("INSERT INTO ledger(batch_id,reference,account_id,delta) VALUES (?,?,?,?)", (batch_id, str(row["reference"]), target, amount))
        db.execute("UPDATE batches SET status='DONE', result=? WHERE id=?", (json.dumps(results), batch_id))
        db.execute("COMMIT")
        return results
    except Exception:
        db.execute("ROLLBACK")
        db.execute("UPDATE batches SET status='FAILED', result=? WHERE id=?", (json.dumps({"error": "LEDGER_WRITE_FAILED"}), batch_id))
        raise
