"""Install Oracle Workers AI credentials without making a model request.

Default mode is read-only. ``--apply`` accepts one owner-staged credential file,
writes a root-only Docker env file, updates only the pinned systemd unit, and
verifies fail-closed HTTPS behavior using nonexistent evidence. It never prints
credentials, changes quota policy, or modifies the public website.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys

import upgrade_oracle_workers_ai as image_release

common = image_release.updater.common
UNIT = common.UNIT
SERVICE = common.SERVICE
CONFIG = common.CONFIG
STAGED = Path('/home/ubuntu/.env.cloudflare-ai-handoff')
CREDENTIALS = CONFIG / 'workers-ai.env'
BACKUP = CONFIG / 'pre-workers-ai-credentials-v1.service'
CURRENT_UNIT = image_release.NEW_UNIT
ENV_ARGUMENT = '--env-file=/etc/codebase-archaeologist/service.env'
AI_ENV_ARGUMENT = '--env-file=/etc/codebase-archaeologist/workers-ai.env'
ACTIVE_UNIT = CURRENT_UNIT.replace(ENV_ARGUMENT, ENV_ARGUMENT + ' ' + AI_ENV_ARGUMENT)
ACCOUNT = re.compile(r'^[a-f0-9]{32}$')
TOKEN = re.compile(r'^[\x21-\x7e]{32,256}$')


def read_staged():
    """Read a regular owner-only staging file without following links."""
    import pwd
    owner = pwd.getpwnam('ubuntu')
    descriptor = os.open(STAGED, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        meta = os.fstat(descriptor)
        common.require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1
            and meta.st_uid == owner.pw_uid and meta.st_gid == owner.pw_gid
            and stat.S_IMODE(meta.st_mode) == 0o600,
            'Staged credential file must be an owner-only regular file.')
        content = os.read(descriptor, 513)
    finally:
        os.close(descriptor)
    common.require(len(content) <= 512, 'Staged credential file exceeds the limit.')
    try:
        text = content.decode('ascii')
    except UnicodeError:
        raise common.UpgradeError('Staged credential file has an invalid format.') from None
    match = re.fullmatch(
        r'ARCHAEOLOGIST_CF_ACCOUNT_ID=([a-f0-9]{32})\n'
        r'ARCHAEOLOGIST_CF_AI_TOKEN=([\x21-\x7e]{32,256})\n', text)
    common.require(match is not None and ACCOUNT.fullmatch(match[1]) is not None
        and TOKEN.fullmatch(match[2]) is not None,
        'Staged credential file has an invalid format.')
    return match[1], match[2]


def write_credentials(account, token):
    common.root_path(CREDENTIALS)
    descriptor = os.open(CREDENTIALS,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        content = (
            'ARCHAEOLOGIST_INTERPRETATION_ENABLED=true\n'
            f'ARCHAEOLOGIST_CF_ACCOUNT_ID={account}\n'
            f'ARCHAEOLOGIST_CF_AI_TOKEN={token}\n'
        ).encode('ascii')
        os.write(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(CONFIG, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def backup_unit():
    if BACKUP.exists() or BACKUP.is_symlink():
        common.require(common.read_root(BACKUP) == CURRENT_UNIT,
            'Existing activation backup differs; refusing overwrite.')
        return
    common.root_path(BACKUP)
    descriptor = os.open(BACKUP,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w', encoding='ascii', newline='\n') as target:
        target.write(CURRENT_UNIT)
        target.flush()
        os.fsync(target.fileno())


def configuration_probe():
    code = (
        "import json,os,re; "
        "a=os.getenv('ARCHAEOLOGIST_CF_ACCOUNT_ID',''); "
        "t=os.getenv('ARCHAEOLOGIST_CF_AI_TOKEN',''); "
        "e=os.getenv('ARCHAEOLOGIST_INTERPRETATION_ENABLED',''); "
        "print(json.dumps({'valid':e=='true' and bool(re.fullmatch(r'[a-f0-9]{32}',a)) "
        "and bool(re.fullmatch(r'[\\x21-\\x7e]{32,256}',t))}))"
    )
    result = common.run('/usr/bin/docker', 'exec', common.CONTAINER, 'python', '-c', code)
    common.require(result == '{"valid": true}',
        'Running backend did not receive a valid private AI configuration.')


def route_checks(token):
    route = '/api/interpret/quota-v1'
    key = 'e' * 64
    for base in ('http://127.0.0.1:8000', common.BASE):
        common.require(common.request(base, route, payload={})[0] == 401,
            'Unauthenticated interpretation was not rejected.')
        common.require(common.request(base, route, token, {}, key)[0] == 400,
            'Configured interpretation input validation differs.')
        common.require(common.request(base, route, token,
            {'reportId': 'R' * 43, 'nodeId': 'symbol:missing'}, key)[0] == 404,
            'Missing evidence did not stop interpretation before inference.')


def preflight(*, active=False):
    common.require(sys.platform.startswith('linux') and os.geteuid() == 0,
        'Run with sudo python3 on Oracle Ubuntu, not Windows.')
    token = common.read_token()
    current = common.read_root(UNIT)
    common.require(current in (CURRENT_UNIT, ACTIVE_UNIT),
        'Service unit differs from the approved bridge versions.')
    common.require(common.run('systemctl', 'show', SERVICE,
        '--property=FragmentPath', '--value') == str(UNIT), 'Unexpected systemd unit path.')
    common.require(not common.run('systemctl', 'show', SERVICE,
        '--property=DropInPaths', '--value'), 'Unreviewed backend overrides exist.')
    common.require(common.run('systemctl', 'show', SERVICE,
        '--property=NeedDaemonReload', '--value') == 'no', 'Pending systemd changes exist.')
    common.inspect_image(image_release.NEW_IMAGE)
    common.runtime(True, image=image_release.NEW_IMAGE)
    common.auth_checks(token)
    if active:
        common.require(current == ACTIVE_UNIT, 'AI credentials are not active.')
        common.require(CREDENTIALS.exists() and stat.S_IMODE(CREDENTIALS.stat().st_mode) == 0o600,
            'Root-only AI credential file is unavailable.')
        common.read_root(CREDENTIALS, 512)
        configuration_probe()
        route_checks(token)
        return token, None
    common.require(current == CURRENT_UNIT, 'AI credentials are already active; use --verify.')
    common.require(not CREDENTIALS.exists() and not CREDENTIALS.is_symlink(),
        'AI credential target already exists.')
    return token, read_staged()


def restore(token, *, remove_credentials):
    common.require(common.read_root(BACKUP) == CURRENT_UNIT,
        'Activation backup differs; refusing restore.')
    common.require(common.read_root(UNIT) in (CURRENT_UNIT, ACTIVE_UNIT),
        'Service changed; refusing overwrite.')
    common.run('systemctl', 'stop', SERVICE)
    common.write_atomic(UNIT, CURRENT_UNIT)
    common.run('systemctl', 'daemon-reload')
    common.run('systemctl', 'start', SERVICE)
    common.wait_health()
    common.runtime(True, image=image_release.NEW_IMAGE)
    common.auth_checks(token)
    if remove_credentials and CREDENTIALS.exists() and not CREDENTIALS.is_symlink():
        CREDENTIALS.unlink()


def apply():
    token, staged = preflight()
    backup_unit()
    account, provider_token = staged
    common.require(common.read_root(UNIT) == CURRENT_UNIT and common.read_token() == token,
        'Configuration changed during preflight.')
    write_credentials(account, provider_token)
    print('Preflight and root-only credential write complete. Activating the private backend route.', flush=True)
    try:
        common.run('systemctl', 'stop', SERVICE)
        common.write_atomic(UNIT, ACTIVE_UNIT)
        common.run('systemctl', 'daemon-reload')
        common.run('systemctl', 'start', SERVICE)
        common.wait_health()
        common.runtime(True, image=image_release.NEW_IMAGE)
        common.auth_checks(token)
        configuration_probe()
        route_checks(token)
        common.require(common.read_token() == token, 'Service token changed during activation.')
    except BaseException:
        print('Credential activation check failed; restoring the disabled backend.', flush=True)
        try:
            restore(token, remove_credentials=True)
        except BaseException:
            raise common.UpgradeError(
                'Rollback could not be verified. Keep SSH open and do not enable the website.') from None
        raise common.UpgradeError(
            'Activation failed; the disabled backend was restored and staged credentials retained.') from None
    STAGED.unlink()
    print('PASS: root-only AI configuration installed; authorization and pre-inference evidence rejection verified.')
    print('The owner staging file was removed. No model request or website change occurred.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.apply:
            apply()
        elif args.verify:
            preflight(active=True)
            print('PASS: active root-only AI configuration verified without inference.')
        else:
            preflight()
            print('PASS: read-only credential activation checks complete. Nothing changed or called.')
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
