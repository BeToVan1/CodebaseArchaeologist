"""Pinned hotspot-scoring release using the tested image-only updater.

Run this entrypoint, not either helper. Default: read-only preflight.
Requires adjacent upgrade_oracle_patterns.py and upgrade_oracle_quota.py.
"""
import signal
import sys

import upgrade_oracle_patterns as updater

OLD_IMAGE = 'sha256:08c6b31f4c9f2b69a0f59b80c892c9fb07d9ee385ebdccc6d1fc92e398bd5777'
NEW_IMAGE = 'sha256:ffb55b2b037b558f3d23a010a555b42ad4d6c89fcbffdf6d5bed57977674f080'
OLD_UNIT = updater.common.UNIT_TEMPLATE.format(run=updater.common.RUN_LINE
    + updater.common.QUOTA_ENV + ' ' + updater.common.MOUNT + ' ' + OLD_IMAGE)
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-hotspot-scoring-v1.service'
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
