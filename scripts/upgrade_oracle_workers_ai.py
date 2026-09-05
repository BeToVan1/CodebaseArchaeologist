"""Pinned Oracle Workers AI bridge image release. Default: read-only preflight.

Requires the adjacent evidence-reference release and its existing upgrade helpers.
This phase changes only the analyzer image. It verifies the interpretation route
is installed but fail-closed; it does not install provider credentials, enable AI,
change quota policy, or modify website files.
"""
import json
import signal
import sys
import urllib.error
import urllib.request

import upgrade_oracle_evidence_reference as previous

updater = previous.updater
OLD_IMAGE = 'sha256:cc2cd37fd541362533d940816a4a0af46ea9b16a46bb3a856ef86bf013e69555'
NEW_IMAGE = 'sha256:c6ec68b6e2f9af8379f20640f475370cc9e1e2fdade4d0df6048f59d168c79f1'
OLD_UNIT = previous.NEW_UNIT
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-workers-ai-bridge-v1.service'
SETTINGS = {name: value for name, value in (
    ('OLD_IMAGE', OLD_IMAGE), ('NEW_IMAGE', NEW_IMAGE),
    ('OLD_UNIT', OLD_UNIT), ('NEW_UNIT', NEW_UNIT), ('BACKUP', BACKUP))}


def disabled_interpretation_response(token):
    """Read only the bounded fail-closed response; never expose its contents."""
    request = urllib.request.Request(
        updater.common.BASE + '/api/interpret/quota-v1',
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token,
            'X-Archaeologist-Client-Key': 'd' * 64,
        },
        data=json.dumps({
            'reportId': 'R' * 43,
            'nodeId': 'symbol:disabled-check',
        }).encode(),
    )
    try:
        with updater.common.HTTP.open(request, timeout=10) as response:
            body = response.read(2049)
            code = response.status
    except urllib.error.HTTPError as error:
        code = error.code
        body = error.read(2049)
        error.close()
    updater.common.require(len(body) <= 2048,
        'Disabled interpretation response exceeded the output limit.')
    return code, body


def real_analysis(token):
    """Reuse the one release analysis, then prove AI remains fail-closed."""
    graph = previous.real_analysis(token)
    code, body = disabled_interpretation_response(token)
    try:
        response = json.loads(body)
    except (ValueError, UnicodeError):
        raise updater.common.UpgradeError(
            'Disabled interpretation route returned invalid JSON.') from None
    updater.common.require(
        code == 503 and response == {'detail': 'AI interpretation is not configured.'},
        'Interpretation route did not remain fail-closed after image update.',
    )
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
