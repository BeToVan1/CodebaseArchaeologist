"""Pinned Workers AI evidence-allowlist release. Default: read-only.

This changes only the analyzer image while preserving the root-only Workers AI
environment file and quota ledger. Validation performs one ordinary repository
analysis plus private configuration and pre-inference evidence checks. It never
calls the model, changes credentials/quota policy, or modifies website files.
"""
import signal
import sys

import upgrade_oracle_workers_ai_diagnostics as previous

updater = previous.updater
OLD_IMAGE = previous.NEW_IMAGE
NEW_IMAGE = 'sha256:2be8fa9b6c9919eda0a4fcd5c675fafa84400933c10ae13bff544afa62a0e669'
OLD_UNIT = previous.NEW_UNIT
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-workers-ai-evidence-allowlist-v1.service'
SETTINGS = {name: value for name, value in (
    ('OLD_IMAGE', OLD_IMAGE), ('NEW_IMAGE', NEW_IMAGE),
    ('OLD_UNIT', OLD_UNIT), ('NEW_UNIT', NEW_UNIT), ('BACKUP', BACKUP))}


def main(argv=None):
    saved = {name: getattr(updater, name) for name in SETTINGS}
    saved_analysis = updater.common.real_analysis
    try:
        for name, value in SETTINGS.items():
            setattr(updater, name, value)
        updater.common.real_analysis = previous.real_analysis
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
