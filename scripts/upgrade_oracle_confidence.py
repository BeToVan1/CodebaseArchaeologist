"""Pinned symbol-confidence release using the tested image-only updater.

Run this entrypoint, not either helper. Default: read-only preflight.
Requires adjacent upgrade_oracle_patterns.py and upgrade_oracle_quota.py.
"""
import signal
import sys

import upgrade_oracle_patterns as updater

OLD_IMAGE = 'sha256:7f4021648d5af75be41a5c7044c0fe5c120145aa3f9d54c38676abd95548a5bf'
NEW_IMAGE = 'sha256:e9cb3a10b15461e67c27d63fef9b8004e26cd6c82fb4b518d9e10bed0e78fe31'
OLD_UNIT = updater.common.UNIT_TEMPLATE.format(run=updater.common.RUN_LINE
    + updater.common.QUOTA_ENV + ' ' + updater.common.MOUNT + ' ' + OLD_IMAGE)
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-symbol-confidence-v1.service'
SETTINGS = {name: value for name, value in (
    ('OLD_IMAGE', OLD_IMAGE), ('NEW_IMAGE', NEW_IMAGE),
    ('OLD_UNIT', OLD_UNIT), ('NEW_UNIT', NEW_UNIT), ('BACKUP', BACKUP))}


def main(argv=None):
    # One owner-run process, fixed release inputs, no arbitrary image argument.
    previous = {name: getattr(updater, name) for name in SETTINGS}
    try:
        for name, value in SETTINGS.items():
            setattr(updater, name, value)
        return updater.main(argv)
    finally:
        for name, value in previous.items():
            setattr(updater, name, value)


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    if sys.platform.startswith('linux'):
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
    raise SystemExit(main())
