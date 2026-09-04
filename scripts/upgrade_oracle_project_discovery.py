"""Owner-approved pinned project-discovery update. Default: read-only preflight.

Requires adjacent upgrade_oracle_patterns.py and upgrade_oracle_quota.py.
No token rotation, quota reset, image pull, or website changes.
"""
import hashlib
import hmac
import json
import re
import signal
import sys

import upgrade_oracle_patterns as updater

OLD_IMAGE = 'sha256:ffb55b2b037b558f3d23a010a555b42ad4d6c89fcbffdf6d5bed57977674f080'
NEW_IMAGE = 'sha256:b49b941d126a2289a6ccf7151e8c3c24b4bfa18427a6a190a5ef6950c85afe40'
OLD_UNIT = updater.common.UNIT_TEMPLATE.format(run=updater.common.RUN_LINE
    + updater.common.QUOTA_ENV + ' ' + updater.common.MOUNT + ' ' + OLD_IMAGE)
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-project-discovery-v1.service'
SETTINGS = {name: value for name, value in (
    ('OLD_IMAGE', OLD_IMAGE), ('NEW_IMAGE', NEW_IMAGE),
    ('OLD_UNIT', OLD_UNIT), ('NEW_UNIT', NEW_UNIT), ('BACKUP', BACKUP))}


def real_analysis(token):
    """One authenticated HTTPS request, with no retry or extra admission."""
    common = updater.common
    key = hmac.new(token.encode(), b'oracle-quota-deployment-validation', hashlib.sha256).hexdigest()
    code, body = common.request(common.BASE, common.ROUTE, token,
        {'repositoryUrl': common.REPO}, key, timeout=75)
    common.require(code == 200, 'The one HTTPS analysis failed; no automatic analysis retry.')
    graph = json.loads(body)
    common.require(graph.get('schema_version') == '1.1'
        and graph.get('analysis', {}).get('tier') == 'deep'
        and graph.get('repository', {}).get('url') == common.REPO
        and re.fullmatch(r'[a-f0-9]{40}', graph.get('snapshot', {}).get('commit_sha', '')) is not None
        and bool(graph.get('nodes')) and bool(graph.get('edges')),
        'HTTPS analysis report identity or content differs.')
    metadata = graph.get('project_discovery') or {}
    common.require(metadata.get('version') == '1' and metadata.get('status') == 'parsed'
        and metadata.get('scope') == 'root-pyproject-only' and metadata.get('path') == 'pyproject.toml'
        and re.fullmatch(r'[a-f0-9]{64}', metadata.get('sha256') or '') is not None
        and bool(metadata.get('limitations')), 'HTTPS project discovery evidence is missing or invalid.')
    declarations = metadata.get('declarations') or []
    common.require(0 < len(declarations) <= 128
        and any(item.get('key') == ['project', 'name'] and item.get('value') == 'itsdangerous' for item in declarations)
        and all(item.get('classification') == 'fact' and item.get('confidence') == 1 for item in declarations),
        'HTTPS project declarations are missing or invalid.')
    return graph


def main(argv=None):
    previous = {name: getattr(updater, name) for name in SETTINGS}
    previous_analysis = updater.common.real_analysis
    try:
        for name, value in SETTINGS.items():
            setattr(updater, name, value)
        updater.common.real_analysis = real_analysis
        return updater.main(argv)
    finally:
        for name, value in previous.items():
            setattr(updater, name, value)
        updater.common.real_analysis = previous_analysis


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    if sys.platform.startswith('linux'):
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
    raise SystemExit(main())
