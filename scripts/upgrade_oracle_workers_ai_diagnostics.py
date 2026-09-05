"""Pinned safe structured-output diagnostics release. Default: read-only.

This changes only the analyzer image while preserving the root-only Workers AI
environment file and quota ledger. Validation proves the private route remains
configured and stops missing evidence before inference. It does not call a model,
change credentials/quota policy, or modify the public website.
"""
import signal
import sys

import configure_oracle_workers_ai as activation
import upgrade_oracle_evidence_reference as evidence_release

updater = activation.image_release.updater
OLD_IMAGE = 'sha256:c6ec68b6e2f9af8379f20640f475370cc9e1e2fdade4d0df6048f59d168c79f1'
NEW_IMAGE = 'sha256:9fb55c24cdbd7eecf5185fcd76c2822a930d535822ffc0a3effb1c78a5bcc9e1'
OLD_UNIT = activation.ACTIVE_UNIT
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = updater.common.CONFIG / 'pre-workers-ai-structured-diagnostics-v1.service'
SETTINGS = {name: value for name, value in (
    ('OLD_IMAGE', OLD_IMAGE), ('NEW_IMAGE', NEW_IMAGE),
    ('OLD_UNIT', OLD_UNIT), ('NEW_UNIT', NEW_UNIT), ('BACKUP', BACKUP))}


def real_analysis(token):
    """One analysis/evidence check, then configured checks with no inference."""
    graph = evidence_release.real_analysis(token)
    activation.configuration_probe()
    activation.route_checks(token)
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
