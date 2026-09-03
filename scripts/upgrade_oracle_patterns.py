"""Pinned image-only update; default is read-only preflight.

Requires the adjacent upgrade_oracle_quota.py helper. Never runs its migration.
No quota initialization, package installation, image pulls, firewall edits,
token rotation, ledger replacement, or Sites deployment.
"""
import argparse
import hashlib
import json
import os
import signal
import sqlite3
import stat
import sys

import upgrade_oracle_quota as common

OLD_IMAGE = 'sha256:b0fa42aa6579e01c8d823abe90d073328de9ffad5869bb0179c7361573478786'
NEW_IMAGE = 'sha256:7f4021648d5af75be41a5c7044c0fe5c120145aa3f9d54c38676abd95548a5bf'
OLD_UNIT = common.UNIT_TEMPLATE.format(run=common.RUN_LINE + common.QUOTA_ENV + ' ' + common.MOUNT + ' ' + OLD_IMAGE)
NEW_UNIT = OLD_UNIT.replace(OLD_IMAGE, NEW_IMAGE)
BACKUP = common.CONFIG / 'pre-pattern-accuracy-v1.service'


def ledger_fingerprint():
    """Read the existing ledger even while the service is stopped; never create it."""
    common.root_path(common.DATA.parent)
    directory = common.DATA.lstat()
    common.require(stat.S_ISDIR(directory.st_mode) and directory.st_uid == 10001
        and directory.st_gid == 10001 and stat.S_IMODE(directory.st_mode) == 0o700,
        'Quota directory ownership or permissions differ.')
    meta = common.LEDGER.lstat()
    common.require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and meta.st_uid == 10001
        and meta.st_gid == 10001 and stat.S_IMODE(meta.st_mode) == 0o600,
        'Quota ledger ownership or permissions differ.')
    db = sqlite3.connect(common.LEDGER.as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        db.execute('PRAGMA query_only=ON')
        common.require(db.execute('PRAGMA quick_check').fetchone() == ('ok',), 'Quota integrity check failed.')
        rows = db.execute('SELECT id,client_key,created_at FROM deep_admissions ORDER BY id').fetchmany(10001)
        common.require(len(rows) <= 10000, 'Quota ledger exceeds the inspection limit.')
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    finally:
        db.close()


def preflight(rollback=False):
    common.require(sys.platform.startswith('linux') and os.geteuid() == 0,
        'Run with sudo python3 on Oracle Ubuntu, not Windows.')
    token = common.read_token()
    current = common.read_root(common.UNIT)
    common.require(current in (OLD_UNIT, NEW_UNIT), 'Service unit differs; no changes made.')
    common.require(common.run('systemctl', 'show', common.SERVICE, '--property=FragmentPath', '--value') == str(common.UNIT),
        'Unexpected systemd unit path.')
    common.require(not common.run('systemctl', 'show', common.SERVICE, '--property=DropInPaths', '--value'),
        'Unreviewed backend overrides exist.')
    common.inspect_image(OLD_IMAGE)
    if not rollback:
        common.require(common.run('systemctl', 'show', common.SERVICE, '--property=NeedDaemonReload', '--value') == 'no',
            'Pending systemd changes must be reviewed first.')
        common.inspect_image(NEW_IMAGE)
        common.runtime(True, image=NEW_IMAGE if current == NEW_UNIT else OLD_IMAGE)
        common.auth_checks(token)
        common.ledger_fingerprint()  # Existing mounted schema, checked by the running service.
    ledger_fingerprint()
    return token, current


def backup_unit():
    if BACKUP.exists() or BACKUP.is_symlink():
        common.require(common.read_root(BACKUP) == OLD_UNIT, 'Backup differs; refusing overwrite.')
        return
    common.root_path(BACKUP)
    descriptor = os.open(BACKUP, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w', encoding='ascii', newline='\n') as target:
        target.write(OLD_UNIT)
        target.flush()
        os.fsync(target.fileno())


def restore(token):
    common.require(common.read_root(BACKUP) == OLD_UNIT, 'Rollback backup differs; refusing restore.')
    common.require(common.read_root(common.UNIT) in (OLD_UNIT, NEW_UNIT), 'Unit changed; refusing overwrite.')
    common.run('systemctl', 'stop', common.SERVICE)
    before = ledger_fingerprint()
    common.write_atomic(common.UNIT, OLD_UNIT)
    common.run('systemctl', 'daemon-reload')
    common.run('systemctl', 'start', common.SERVICE)
    common.wait_health()
    common.runtime(True, image=OLD_IMAGE)
    common.auth_checks(token)
    common.require(ledger_fingerprint() == before, 'Ledger changed during rollback verification.')
    common.require(common.read_token() == token, 'Token changed during rollback.')
    print('ROLLBACK PASS: previous quota-enabled image restored; ledger retained.', flush=True)


def apply():
    token, current = preflight()
    common.require(current == OLD_UNIT, 'Update already installed; use --verify.')
    backup_unit()
    common.require(common.read_root(common.UNIT) == OLD_UNIT and common.read_token() == token,
        'Configuration changed during preflight.')
    print('Preflight and backup complete. Updating only the analyzer image.', flush=True)
    try:
        common.run('systemctl', 'stop', common.SERVICE)
        before = ledger_fingerprint()
        common.write_atomic(common.UNIT, NEW_UNIT)
        common.run('systemctl', 'daemon-reload')
        common.run('systemctl', 'start', common.SERVICE)
        common.wait_health()
        common.runtime(True, image=NEW_IMAGE)
        common.auth_checks(token)
        common.require(ledger_fingerprint() == before,
            'Ledger changed across image replacement; concurrent traffic may have changed admissions.')
        common.real_analysis(token)  # One request; never automatically retried.
        common.require(ledger_fingerprint() != before, 'Analysis did not persist a quota admission.')
        common.runtime(True, image=NEW_IMAGE)
        common.require(common.read_token() == token, 'Token changed during the update.')
    except BaseException:
        print('Update check failed; attempting rollback without replacing quota data.', flush=True)
        try:
            restore(token)
        except BaseException:
            raise common.UpgradeError('Rollback could not be verified. Keep SSH open; review before using --rollback.') from None
        raise common.UpgradeError('Update failed; previous backend restored.') from None
    print('PASS: analyzer image upgraded; verified HTTPS analysis, authorization and quota preservation.')
    print(f'Backup: {BACKUP}. Keep the old image. Website files unchanged.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--verify', action='store_true')
    modes.add_argument('--rollback', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.apply:
            apply()
        elif args.rollback:
            token, _ = preflight(rollback=True)
            restore(token)
        else:
            _, current = preflight()
            if args.verify:
                common.require(current == NEW_UNIT, 'New analyzer image is not installed.')
            print('PASS: read-only image-update checks complete. No changes or analysis jobs submitted.')
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print('STOP: ' + (str(error) if isinstance(error, common.UpgradeError)
            else type(error).__name__ + '; private details withheld.'))
        return 1


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    if sys.platform.startswith('linux'):
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
    raise SystemExit(main())
