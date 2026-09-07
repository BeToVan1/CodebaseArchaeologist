"""Pinned uncertainty-placeholder validation release; default is read-only preflight.

Preserves credentials and quota data. Apply performs one repository analysis,
never model inference. Uses the existing image-only rollback workflow.
"""
import signal
import sys

import upgrade_oracle_local_execution as previous
from upgrade_oracle_workers_ai_diagnostics import real_analysis

updater = previous.updater
OLD_IMAGE = previous.NEW_IMAGE
NEW_IMAGE = 'sha256:0aacca4abb7ac2bb82e872378e08ec973a8d67aa6da19dec2f2915fe1ca2d62c'
OLD_UNIT = previous.NEW_UNIT
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-uncertainty-validation-v1.service'
SETTINGS = {name: value for name, value in (
    ('OLD_IMAGE', OLD_IMAGE), ('NEW_IMAGE', NEW_IMAGE),
    ('OLD_UNIT', OLD_UNIT), ('NEW_UNIT', NEW_UNIT), ('BACKUP', BACKUP))}


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
