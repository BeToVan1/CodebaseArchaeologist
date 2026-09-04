"""Pinned test-proximity release. Default: read-only preflight.

Requires adjacent upgrade_oracle_project_discovery.py, upgrade_oracle_patterns.py,
upgrade_oracle_quota.py and container_smoke.py. No website or quota policy changes.
"""
import signal
import sys

import upgrade_oracle_project_discovery as previous
from container_smoke import check_test_proximity

updater = previous.updater
OLD_IMAGE = 'sha256:b49b941d126a2289a6ccf7151e8c3c24b4bfa18427a6a190a5ef6950c85afe40'
NEW_IMAGE = 'sha256:a64484345dd1943c2df908a51d66e27ddd8d2f67aa3b25bff7de2cc6482a2112'
OLD_UNIT = updater.common.UNIT_TEMPLATE.format(run=updater.common.RUN_LINE
    + updater.common.QUOTA_ENV + ' ' + updater.common.MOUNT + ' ' + OLD_IMAGE)
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-test-proximity-v1.service'
SETTINGS = {name: value for name, value in (
    ('OLD_IMAGE', OLD_IMAGE), ('NEW_IMAGE', NEW_IMAGE),
    ('OLD_UNIT', OLD_UNIT), ('NEW_UNIT', NEW_UNIT), ('BACKUP', BACKUP))}


def real_analysis(token):
    # Reuse the single HTTPS request; do not submit a second analysis.
    graph = previous.real_analysis(token)
    try:
        check_test_proximity(graph)
    except (AssertionError, KeyError, TypeError, ValueError, AttributeError):
        raise updater.common.UpgradeError('HTTPS test proximity evidence is missing or invalid.') from None


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
