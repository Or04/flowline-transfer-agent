"""HTTP client retries ambiguous 500 responses using stable idempotency keys."""
import hashlib
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .ingestion import parse_file

class ApiError(RuntimeError): pass

class TransferClient:
    def __init__(self, base_url, retries=3):
        self.base_url = base_url.rstrip("/")
        self.retries = retries

    def request(self, method, path, payload=None, headers=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base_url + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            if exc.code >= 500:
                raise ApiError(f"Transient HTTP {exc.code}") from exc
            raise ValueError(f"HTTP {exc.code}: {exc.read().decode()}") from exc
        except (URLError, TimeoutError) as exc:
            raise ApiError(str(exc)) from exc

    def submit(self, path):
        parsed = parse_file(path)
        payload = {"transfers": parsed["transfers"]}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = "sha256:" + digest
        for attempt in range(self.retries + 1):
            try:
                _, result = self.request("POST", "/batches", payload, {"Idempotency-Key": key})
                return result
            except ApiError:
                if attempt == self.retries:
                    raise
                time.sleep(0.01 * (attempt + 1))

    def status(self, batch_id):
        return self.request("GET", f"/batches/{batch_id}")[1]

    def summary(self):
        return self.request("GET", "/ledger/summary")[1]

    def reset(self, accounts):
        return self.request("POST", "/__reset", {"accounts": accounts})[1]
