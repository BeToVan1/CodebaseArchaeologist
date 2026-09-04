"""Pinned server-evidence release. Default: read-only preflight.

Requires adjacent upgrade_oracle_test_proximity.py and its existing upgrade
helpers. This changes only the analyzer image. It does not enable a model,
change quota policy, rotate credentials, or modify website files.
"""
import hashlib
import hmac
import json
import re
import signal
import sys
import urllib.error
import urllib.request

import upgrade_oracle_test_proximity as previous

updater = previous.updater
OLD_IMAGE = 'sha256:a64484345dd1943c2df908a51d66e27ddd8d2f67aa3b25bff7de2cc6482a2112'
NEW_IMAGE = 'sha256:cc2cd37fd541362533d940816a4a0af46ea9b16a46bb3a856ef86bf013e69555'
OLD_UNIT = updater.common.UNIT_TEMPLATE.format(run=updater.common.RUN_LINE
    + updater.common.QUOTA_ENV + ' ' + updater.common.MOUNT + ' ' + OLD_IMAGE)
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-evidence-reference-v1.service'
SETTINGS = {name: value for name, value in (
    ('OLD_IMAGE', OLD_IMAGE), ('NEW_IMAGE', NEW_IMAGE),
    ('OLD_UNIT', OLD_UNIT), ('NEW_UNIT', NEW_UNIT), ('BACKUP', BACKUP))}


def analyze_with_headers(token, key):
    """Submit exactly one analysis and retain only its response headers/body."""
    common = updater.common
    request = urllib.request.Request(
        common.BASE + common.ROUTE,
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token,
            'X-Archaeologist-Client-Key': key,
        },
        data=json.dumps({'repositoryUrl': common.REPO}).encode(),
    )
    try:
        with common.HTTP.open(request, timeout=75) as response:
            body = response.read(10 * 1024 * 1024 + 1)
            common.require(len(body) <= 10 * 1024 * 1024,
                'Report exceeded the output limit.')
            return response.status, body, response.headers
    except urllib.error.HTTPError as error:
        code, headers = error.code, error.headers
        error.close()
        return code, b'', headers


def real_analysis(token):
    """One quota admission, followed by owner-binding and evidence checks."""
    common = updater.common
    key = hmac.new(token.encode(), b'oracle-quota-deployment-validation', hashlib.sha256).hexdigest()
    code, body, headers = analyze_with_headers(token, key)
    common.require(code == 200, 'The one HTTPS analysis failed; no automatic analysis retry.')
    try:
        graph = json.loads(body)
    except (ValueError, UnicodeError):
        raise common.UpgradeError('HTTPS analysis returned invalid JSON.') from None
    commit = graph.get('snapshot', {}).get('commit_sha', '')
    common.require(graph.get('schema_version') == '1.1'
        and graph.get('analysis', {}).get('tier') == 'deep'
        and graph.get('repository', {}).get('url') == common.REPO
        and re.fullmatch(r'[a-f0-9]{40}', commit) is not None
        and bool(graph.get('nodes')) and bool(graph.get('edges')),
        'HTTPS analysis report identity or content differs.')
    report_id = headers.get('X-Archaeologist-Report-Id', '')
    common.require(re.fullmatch(r'[A-Za-z0-9_-]{43}', report_id) is not None
        and headers.get('X-Archaeologist-Report-TTL') == '900'
        and headers.get('Cache-Control') == 'no-store',
        'HTTPS analysis did not return a bounded opaque evidence reference.')
    symbols = [node for node in graph['nodes'] if isinstance(node.get('evidence_packet'), dict)]
    common.require(bool(symbols), 'HTTPS analysis has no symbol evidence packet to prepare.')
    symbol = symbols[0]
    selection = {'reportId': report_id, 'nodeId': symbol.get('id')}

    wrong_key = hmac.new(token.encode(), b'oracle-evidence-wrong-owner', hashlib.sha256).hexdigest()
    common.require(common.request(common.BASE, '/api/evidence/prepare', token,
        selection, wrong_key, timeout=10)[0] == 404,
        'Evidence reference was not isolated from a different owner key.')
    prepared_code, prepared_body = common.request(common.BASE, '/api/evidence/prepare',
        token, selection, key, timeout=10)
    common.require(prepared_code == 200, 'Owner-bound evidence preparation failed.')
    try:
        prepared = json.loads(prepared_body)
    except (ValueError, UnicodeError):
        raise common.UpgradeError('Prepared evidence returned invalid JSON.') from None
    excerpt = prepared.get('sourceExcerpt')
    common.require(prepared.get('commitSha') == commit
        and prepared.get('evidencePacket') == symbol['evidence_packet']
        and isinstance(excerpt, str) and 0 < len(excerpt) <= 12000,
        'Prepared evidence does not match the retained analysis snapshot.')
    return graph


def main(argv=None):
    saved = {name: getattr(updater, name) for name in SETTINGS}
    saved_analysis = updater.common.real_analysis
    try:
        for name, value in SETTINGS.items():
            setattr(updater, name, value)
        updater.common.real_analysis = real_analysis
        return updater.main(argv)
    finally:
        for name, value in saved.items():
            setattr(updater, name, value)
        updater.common.real_analysis = saved_analysis


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    if sys.platform.startswith('linux'):
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
    raise SystemExit(main())
