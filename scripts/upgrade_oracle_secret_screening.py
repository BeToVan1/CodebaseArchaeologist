"""Pinned credential-screening release; default is read-only preflight.

Preserves credentials and quota data. Apply performs one repository analysis,
never model inference. Uses the existing image-only rollback workflow.
"""
import signal
import sys

import upgrade_oracle_workers_ai_evidence_allowlist as previous

updater = previous.updater
OLD_IMAGE = previous.NEW_IMAGE
NEW_IMAGE = 'sha256:a6174b5914dd3ad10d4366dcfbadce8aebe3d86e62d128e9bfb18aed3ba567e2'
OLD_UNIT = previous.NEW_UNIT
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-secret-screening-v1.service'
SETTINGS = {name: value for name, value in (
    ('OLD_IMAGE', OLD_IMAGE), ('NEW_IMAGE', NEW_IMAGE),
    ('OLD_UNIT', OLD_UNIT), ('NEW_UNIT', NEW_UNIT), ('BACKUP', BACKUP))}


def main(argv=None):
    saved = {name: getattr(updater, name) for name in SETTINGS}
    saved_analysis = updater.common.real_analysis
    try:
        for name, value in SETTINGS.items():
            setattr(updater, name, value)
        updater.common.real_analysis = previous.previous.real_analysis
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
