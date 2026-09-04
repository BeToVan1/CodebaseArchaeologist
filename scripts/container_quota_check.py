"""Offline runtime-image probe; three modes run in separate disposable containers.

No GitHub jobs, published ports, real credentials, or production ledger access.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

from deep_quota import initialize, reserve, QuotaUnavailable

LEDGER = Path('/quota/quota.sqlite3')
KEY = 'a' * 64


def seed():
    if LEDGER.exists():
        raise RuntimeError('Expected a fresh test ledger.')
    initialize(LEDGER)
    for _ in range(3):
        if not reserve(LEDGER, KEY):
            raise RuntimeError('Test admission unexpectedly denied.')
    if reserve(LEDGER, KEY):
        raise RuntimeError('Network quota exceeded.')


def probe(mode):
    if mode == 'persisted':
        # Do not initialize here: missing persistence must cause a failure.
        if reserve(LEDGER, KEY):
            raise RuntimeError('Replacement container reset the allowance.')
        expected = 429
    else:
        if LEDGER.exists():
            raise RuntimeError('Missing-mount test unexpectedly has a ledger.')
        try:
            reserve(LEDGER, KEY)
        except QuotaUnavailable:
            pass
        else:
            raise RuntimeError('Missing storage admitted work.')
        expected = 503
    token = secrets.token_hex(32)
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'deep_service:create_app',
        '--factory', '--host', '127.0.0.1', '--port', '8000', '--workers', '1', '--no-access-log'],
        env={**os.environ, 'ARCHAEOLOGIST_SERVICE_TOKEN': token, 'ARCHAEOLOGIST_QUOTA_PATH': str(LEDGER)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, authenticated=True, payload=None):
        headers = {'Content-Type': 'application/json', 'X-Archaeologist-Client-Key': KEY}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request('http://127.0.0.1:8000' + path, headers=headers,
            data=None if payload is None else json.dumps(payload).encode())
        try:
            with http.open(req, timeout=2) as response:
                return response.status, response.headers.get('X-Archaeologist-Limit')
        except urllib.error.HTTPError as error:
            status, marker = error.code, error.headers.get('X-Archaeologist-Limit')
            error.close()
            return status, marker

    try:
        for _ in range(50):
            if server.poll() is not None:
                raise RuntimeError('Service exited during startup.')
            try:
                if request('/health')[0] == 200:
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionResetError):
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError('Service startup timed out.')
        payload = {'repositoryUrl': 'https://github.com/pallets/itsdangerous'}
        for route in ('/api/analyze/quota-v1', '/api/analyze'):
            if request(route, False, payload)[0] != 401:
                raise RuntimeError('Authorization check failed.')
            status, marker = request(route, True, payload)
            if status != expected or (expected == 429 and marker != 'quota'):
                raise RuntimeError('Storage boundary returned an unexpected status.')
        if mode == 'persisted':
            with closing(sqlite3.connect(LEDGER.as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('SELECT count(*) FROM deep_admissions').fetchone()[0] != 3:
                    raise RuntimeError('Denied requests changed the ledger.')
        elif LEDGER.exists():
            raise RuntimeError('Service recreated a missing ledger.')
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['seed', 'persisted', 'missing'])
    args = parser.parse_args()
    try:
        seed() if args.mode == 'seed' else probe(args.mode)
    except Exception:
        raise SystemExit('STOP: offline quota probe failed; no production state was touched.') from None
    print(json.dumps({'result': 'PASS', 'quota_check': args.mode, 'analysis_jobs_submitted': 0}))
