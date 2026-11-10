"""Self-contained local product dashboard backed by the real mock ledger."""
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .mock import MockState
from .ingestion import parse_file
from .client import TransferClient

ROOT = Path(__file__).resolve().parents[1]
HTML = Path(__file__).parent / 'static' / 'index.html'

class DashboardServer:
    def __init__(self, port=8787, db_path=':memory:'):
        self.state = MockState(db_path)
        self.http = ThreadingHTTPServer(('127.0.0.1', port), self.handler())
        self.url = f'http://127.0.0.1:{self.http.server_port}'
    def handler(self):
        state = self.state
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def reply(self, code, value):
                payload = json.dumps(value).encode()
                self.send_response(code)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(payload)))
                self.end_headers(); self.wfile.write(payload)
            def do_GET(self):
                path = urlsplit(self.path).path
                if path == '/':
                    data = HTML.read_bytes()
                    self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8')
                    self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
                elif path == '/api/state':
                    with state.lock:
                        batches = [state.batch(r['id']) for r in state.ledger.db.execute('SELECT id FROM batches ORDER BY rowid DESC')]
                        accounts = [state.account(r['id']) for r in state.ledger.db.execute('SELECT id FROM accounts ORDER BY id')]
                        self.reply(200, {'summary':state.ledger.summary(),'accounts':accounts,'batches':batches})
                else: self.reply(404, {'error':'Not found'})
            def do_POST(self):
                path = urlsplit(self.path).path
                try:
                    length = int(self.headers.get('Content-Length','0'))
                    if length > 2_000_000: return self.reply(413, {'error':'Payload too large'})
                    body = json.loads(self.rfile.read(length)) if length else {}
                    with state.lock:
                        if path == '/api/reset':
                            state.reset(parse_file(ROOT/'transfers.json')['accounts'])
                            return self.reply(200, {'ok':True})
                        if path == '/api/submit':
                            import hashlib
                            payload = {'transfers': parse_file(ROOT/'transfers.json')['transfers']}
                            digest = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                            batch_id = state.submit(payload, 'sha256:'+digest)
                            return self.reply(200, state.batch(batch_id))
                        if path == '/api/fault':
                            n = body.get('failOnWrite')
                            if type(n) is not int or n < 1: raise ValueError('failOnWrite must be a positive integer')
                            state.fail_write = n
                            return self.reply(200, {'ok':True})
                    self.reply(404, {'error':'Not found'})
                except (ValueError, KeyError, TypeError) as e: self.reply(400, {'error':str(e)})
        return Handler
    def __enter__(self):
        import threading
        self.thread = threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start(); return self
    def __exit__(self,*_):
        self.http.shutdown(); self.http.server_close(); self.thread.join()

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8787);p.add_argument('--db',default=':memory:');a=p.parse_args()
    with DashboardServer(a.port,a.db) as app:
        print(f'Transfer Flow dashboard: {app.url}',flush=True)
        try: app.thread.join()
        except KeyboardInterrupt: pass
if __name__=='__main__': main()
