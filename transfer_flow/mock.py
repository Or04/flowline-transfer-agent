"""Local HTTP mock with deterministic failure injection."""
import hashlib
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .ledger import Ledger, money
from .service import process_batch, WriteFailure

class MockState:
    def __init__(self, db_path=":memory:"):
        self.ledger = Ledger(db_path)
        self.fail500 = 0
        self.fail_write = None
        self.lock = threading.RLock()

    def reset(self, accounts):
        with self.lock:
            self.ledger.reset(accounts)
            self.fail500 = 0
            self.fail_write = None

    def submit(self, payload, key):
        if not isinstance(payload, dict) or not isinstance(payload.get("transfers"), list):
            raise ValueError("Expected transfers array")
        if not key:
            raise ValueError("Idempotency-Key required")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        # key scoped to content: a retry must carry the same payload
        with self.lock:
            existing = self.ledger.db.execute("SELECT id,result,status FROM batches WHERE request_key=?", (key,)).fetchone()
            if existing:
                if not key.endswith(digest):
                    raise ValueError("Idempotency key/payload mismatch")
                return existing["id"]
            if not key.endswith(digest):
                raise ValueError("Idempotency key/payload mismatch")
            batch_id = "B-" + uuid.uuid4().hex[:12]
            self.ledger.db.execute("INSERT INTO batches VALUES (?,?,?,NULL)", (batch_id, key, "PENDING"))
            self.ledger.db.execute("UPDATE batches SET status='PROCESSING' WHERE id=?", (batch_id,))
            failure = self.fail_write
            self.fail_write = None
            try:
                process_batch(self.ledger, batch_id, payload["transfers"], failure)
            except WriteFailure:
                pass
            return batch_id

    def batch(self, batch_id):
        r = self.ledger.db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if r is None:
            return None
        return {"batchId": r["id"], "status": r["status"], "results": json.loads(r["result"]) if r["result"] else None}

    def account(self, account_id):
        r = self.ledger.db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return {"id": r["id"], "balance": money(r["balance"]), "currency": r["currency"]} if r else None


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def send_json(self, code, value):
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def dispatch(self, method):
            path = urlsplit(self.path).path
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size > 2_000_000:
                    return self.send_json(413, {"error": "Too large"})
                body = json.loads(self.rfile.read(size)) if size else {}
                with state.lock:
                    if path == "/__reset" and method == "POST":
                        state.reset(body["accounts"])
                        return self.send_json(200, {"ok": True})
                    if path == "/__faults" and method == "POST":
                        if "failOnWrite" in body:
                            n = body["failOnWrite"]
                            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                                raise ValueError("failOnWrite must be positive integer")
                            state.fail_write = n
                        if "fail500Next" in body:
                            n = body["fail500Next"]
                            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                                raise ValueError("fail500Next must be nonnegative integer")
                            state.fail500 = n
                        return self.send_json(200, {"ok": True})
                    # Fault is applied AFTER accepting POST /batches, but BEFORE responding.
                    if path == "/batches" and method == "POST":
                        batch_id = state.submit(body, self.headers.get("Idempotency-Key"))
                        if state.fail500:
                            state.fail500 -= 1
                            return self.send_json(500, {"error": "Simulated lost response"})
                        return self.send_json(202, {"batchId": batch_id})
                    if state.fail500:
                        state.fail500 -= 1
                        return self.send_json(500, {"error": "Injected 500"})
                    if method == "GET" and path.startswith("/batches/"):
                        value = state.batch(path.split("/")[-1])
                        return self.send_json(200 if value else 404, value or {"error": "Not found"})
                    if method == "GET" and path.startswith("/accounts/"):
                        value = state.account(path.split("/")[-1])
                        return self.send_json(200 if value else 404, value or {"error": "Not found"})
                    if method == "GET" and path == "/ledger/summary":
                        return self.send_json(200, state.ledger.summary())
                    return self.send_json(404, {"error": "Not found"})
            except (ValueError, KeyError, TypeError) as exc:
                return self.send_json(400, {"error": str(exc)})
        def do_POST(self): self.dispatch("POST")
        def do_GET(self): self.dispatch("GET")
    return Handler

class MockServer:
    def __init__(self, db_path=":memory:", port=0):
        self.state = MockState(db_path)
        self.http = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.state))
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, *_):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--db", default="transfers.db")
    args = p.parse_args()
    with MockServer(args.db, args.port) as server:
        print(f"Mock listening on {server.url}", flush=True)
        try:
            server.thread.join()
        except KeyboardInterrupt:
            pass

if __name__ == "__main__": main()
