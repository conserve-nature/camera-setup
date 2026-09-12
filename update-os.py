#!/usr/bin/env python3
"""Unattended APT maintenance and adjacent Debian stable release upgrades."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

STATE = Path('/var/lib/trail-camera-updates')
APT = Path('/etc/apt')
CONFIG = Path('/etc/trail-camera-updates.json')
# List stale services; the final reboot refreshes them. Restarting this updater
# from a needrestart hook would deadlock APT against its own systemd service.
ENV = dict(os.environ, DEBIAN_FRONTEND='noninteractive', APT_LISTCHANGES_FRONTEND='none',
           NEEDRESTART_MODE='l', UCF_FORCE_CONFFOLD='1')
APT_OPTIONS = ['-q', '-o', 'DPkg::Lock::Timeout=600', '-o', 'Acquire::Retries=3',
               '-o', 'Acquire::http::Timeout=60', '-o', 'Acquire::https::Timeout=60',
               '-o', 'APT::Update::Error-Mode=any',
               '-o', 'Acquire::AllowInsecureRepositories=false',
               '-o', 'Acquire::AllowDowngradeToInsecureRepositories=false',
               '-o', 'APT::Get::AllowUnauthenticated=false',
               '-o', 'Dpkg::Options::=--force-confdef',
               '-o', 'Dpkg::Options::=--force-confold']


def log(message):
    print(time.strftime('%Y-%m-%dT%H:%M:%S%z'), message, flush=True)


def run(args, capture=False):
    log('+ ' + shlex.join(str(arg) for arg in args))
    return subprocess.run([str(arg) for arg in args], check=True, env=ENV,
                          stdin=subprocess.DEVNULL, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def apt(*args, extra=()):
    return run(['apt-get', *APT_OPTIONS, *extra, *args])


def atomic_json(path, data):
    atomic_write(path, json.dumps(data, indent=2) + '\n')


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def os_release():
    fields = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            fields[key] = shlex.split(value)[0]
    if fields.get('ID') != 'debian':
        raise RuntimeError('Only Debian-based 64-bit Raspberry Pi / Debian is supported.')
    return fields['VERSION_CODENAME'], int(fields['VERSION_ID'].split('.')[0])


def stable_release():
    """Authenticate discovery separately from APT's target archive verification."""
    keyring = next((p for p in (Path('/usr/share/keyrings/debian-archive-keyring.pgp'),
                               Path('/usr/share/keyrings/debian-archive-keyring.gpg'))
                    if p.exists()), None)
    if keyring is None:
        raise RuntimeError('Install debian-archive-keyring first.')
    with tempfile.TemporaryDirectory() as tmp:
        signed, verified = Path(tmp) / 'InRelease', Path(tmp) / 'Release'
        with urllib.request.urlopen('https://deb.debian.org/debian/dists/stable/InRelease',
                                    timeout=60) as response:
            payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise RuntimeError('Oversized release metadata.')
        signed.write_bytes(payload)
        run(['gpgv', '--keyring', keyring, '--output', verified, signed])
        fields = dict(re.findall(r'^([A-Za-z-]+): (.+)$', verified.read_text(), re.M))
    if fields.get('Origin') != 'Debian' or fields.get('Suite') != 'stable':
        raise RuntimeError('Discovery metadata is not Debian stable.')
    codename = fields.get('Codename', '')
    version = fields.get('Version', '')
    if not re.fullmatch(r'[a-z]+', codename) or not re.fullmatch(r'\d+(?:\.\d+)*', version):
        raise RuntimeError('Invalid stable release identity.')
    return codename, int(version.split('.')[0])


def target_release(current, stable):
    if stable[1] < current[1]:
        raise RuntimeError('Installed release is newer than discovered stable; refusing downgrade.')
    if stable[1] == current[1]:
        if stable[0] != current[0]:
            raise RuntimeError('Conflicting release identity.')
        return None
    if stable[1] != current[1] + 1:
        raise RuntimeError('Only adjacent major upgrades are supported; intermediate release required.')
    return stable[0]


def source_files(root=APT):
    paths = [root / 'sources.list']
    for extension in ('*.list', '*.sources'):
        paths.extend(sorted((root / 'sources.list.d').glob(extension)))
    return {str(path.relative_to(root)): path.read_text() for path in paths if path.exists()}


def known_uri(uri):
    parsed = urlsplit(uri)
    return (parsed.scheme in ('http', 'https') and not parsed.username and
            not parsed.query and not parsed.fragment and
            (parsed.hostname, parsed.path.rstrip('/')) in {
                ('deb.debian.org', '/debian'), ('deb.debian.org', '/debian-security'),
                ('security.debian.org', '/debian-security'),
                ('archive.raspberrypi.com', '/debian'),
                ('archive.raspberrypi.org', '/debian'),
                ('download.docker.com', '/linux/debian')})


def migrate_sources(files, current, target):
    """Only rewrite suite fields in known, codename-pinned archives. Preserve options."""
    active = 0

    def suites(uris, names):
        nonlocal active
        if not uris or not all(known_uri(uri) for uri in uris):
            raise RuntimeError('Unsupported APT archive: ' + ' '.join(uris))
        allowed = {current + suffix: target + suffix
                   for suffix in ('', '-updates', '-security', '-backports')}
        if not names or any(name not in allowed for name in names):
            raise RuntimeError('Sources must use the installed codename: ' + ' '.join(names))
        active += 1
        return ' '.join(allowed[name] for name in names)

    result = {}
    for filename, text in files.items():
        if filename.endswith('.sources'):
            paragraphs = re.split(r'(\n[ \t]*\n)', text)
            for index in range(0, len(paragraphs), 2):
                block = paragraphs[index]
                clean = '\n'.join(line for line in block.splitlines()
                                  if not line.lstrip().startswith('#'))
                if not clean.strip():
                    continue
                fields = {}
                last = None
                for line in clean.splitlines():
                    if line.startswith((' ', '\t')) and last:
                        fields[last] += ' ' + line.strip()
                    elif ':' in line:
                        key, value = line.split(':', 1)
                        last = key.lower()
                        if last in fields:
                            raise RuntimeError('Duplicate source field: ' + key)
                        fields[last] = value.strip()
                    else:
                        raise RuntimeError('Unrecognized deb822 source syntax.')
                if fields.get('enabled', 'yes').lower() == 'no':
                    continue
                if set(fields.get('types', '').split()) - {'deb', 'deb-src'} or not fields.get('types'):
                    raise RuntimeError('Unsupported source type.')
                if any(fields.get(key, 'no').lower() != 'no'
                       for key in ('trusted', 'allow-insecure', 'allow-weak', 'allow-downgrade-to-insecure')):
                    raise RuntimeError('Insecure archive options are not supported.')
                replacement = suites(fields.get('uris', '').split(), fields.get('suites', '').split())
                paragraphs[index], count = re.subn(
                    r'(?im)^Suites:[^\n]*(?:\n[ \t]+[^\n]*)*', 'Suites: ' + replacement, block)
                if count != 1:
                    raise RuntimeError('Expected one Suites field.')
            result[filename] = ''.join(paragraphs)
        else:
            lines = []
            for line in text.splitlines(keepends=True):
                if not line.strip() or line.lstrip().startswith('#'):
                    lines.append(line)
                    continue
                match = re.match(r'^(\s*deb(?:-src)?\s+(?:\[[^\]]+\]\s+)?)(\S+)(\s+)(\S+)(.*)$',
                                 line.rstrip('\n'))
                if not match:
                    raise RuntimeError('Unrecognized sources.list syntax.')
                unsafe_options = re.findall(
                    r'(?:trusted|allow-insecure|allow-weak|allow-downgrade-to-insecure)\s*=\s*([^\s\]]+)', match[1])
                if any(value.lower() != 'no' for value in unsafe_options):
                    raise RuntimeError('Insecure archive options are not supported.')
                lines.append(match[1] + match[2] + match[3] + suites([match[2]], [match[4]]) +
                             match[5] + ('\n' if line.endswith('\n') else ''))
            result[filename] = ''.join(lines)
    if active == 0:
        raise RuntimeError('No enabled supported APT repositories.')
    return result


def validate_space(major=False):
    for path, minimum in (('/', 3 * 1024**3 if major else 1024**3),
                          ('/boot/firmware', 200 * 1024**2), ('/boot', 200 * 1024**2)):
        if Path(path).exists() and shutil.disk_usage(path).free < minimum:
            raise RuntimeError(f'Insufficient free space on {path}; need at least {minimum // 1024**2} MiB.')


def healthy():
    audit = run(['dpkg', '--audit'], capture=True)
    if audit.strip():
        raise RuntimeError('dpkg requires recovery:\n' + audit)
    apt('check')


def recover_dpkg():
    try:
        run(['dpkg', '--force-confdef', '--force-confold', '--configure', '-a'])
    except subprocess.CalledProcessError:
        # Unpacked packages can require dependencies that APT still needs to fetch.
        log('dpkg configuration incomplete; attempting dependency repair with APT.')


def fingerprint():
    packages = run(['dpkg-query', '-W', '-f=${binary:Package} ${Version} ${db:Status-Status}\n'], capture=True)
    return hashlib.sha256(packages.encode()).hexdigest()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def probe(files, simulate=False, expected_release=None):
    """Use isolated source lists, indexes and caches; authenticate all target archives."""
    with tempfile.TemporaryDirectory(prefix='trail-camera-apt-') as directory:
        root = Path(directory)
        root.chmod(0o755)
        (root / 'sources.list.d').mkdir()
        (root / 'sources.list').touch()
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(content)
        (root / 'lists' / 'partial').mkdir(parents=True)
        (root / 'archives' / 'partial').mkdir(parents=True)
        options = ['-o', f'Dir::Etc::sourcelist={root / "sources.list"}',
                   '-o', f'Dir::Etc::sourceparts={root / "sources.list.d"}',
                   '-o', f'Dir::State::lists={root / "lists"}',
                   '-o', f'Dir::Cache::archives={root / "archives"}',
                   '-o', 'Dir::Cache::pkgcache=', '-o', 'Dir::Cache::srcpkgcache=']
        apt('update', extra=options)
        if expected_release:
            # APT can only warn when a server returns a different signed suite.
            # Do not mistake that for the vendor publishing our target release.
            releases = list((root / 'lists').glob('*InRelease'))
            if not releases:
                raise RuntimeError('No authenticated InRelease indexes were fetched.')
            architecture = run(['dpkg', '--print-architecture'], capture=True).strip()
            for release in releases:
                fields = dict(re.findall(r'^([A-Za-z-]+): (.+)$', release.read_text(), re.M))
                allowed = {expected_release + suffix for suffix in ('', '-updates', '-security', '-backports')}
                if fields.get('Codename') not in allowed or architecture not in fields.get('Architectures', '').split():
                    raise RuntimeError('Archive does not publish the expected release/architecture: ' + release.name)
        if simulate:
            apt('-s', 'dist-upgrade', extra=options)


def snapshot():
    folder = STATE / ('backup-' + time.strftime('%Y%m%dT%H%M%S'))
    folder.mkdir(mode=0o700)
    with tarfile.open(folder / 'host-state.tar.gz', 'w:gz') as archive:
        for path in ('/etc', '/var/lib/dpkg', '/var/lib/apt/extended_states'):
            if Path(path).exists():
                archive.add(path, arcname=path.lstrip('/'))
    (folder / 'packages.txt').write_text(run(['dpkg', '--get-selections'], capture=True))
    for path in (*folder.iterdir(), folder, STATE):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return str(folder)


def perform_major(transaction):
    # Saved target files also repair a power loss between individual source replacements.
    for name, content in transaction['sources'].items():
        atomic_write(APT / name, content)
    apt('update', '--allow-releaseinfo-change')
    recover_dpkg()
    apt('-y', '-f', 'install')
    apt('-y', 'upgrade', '--without-new-pkgs')
    apt('-y', 'dist-upgrade')
    healthy()
    actual = os_release()
    if actual[0] != transaction['target']:
        raise RuntimeError('Major upgrade did not reach target release.')
    # A resumed upgrade may be running on a different boot from its first attempt.
    # Require a boot AFTER the final successful package transaction.
    transaction['boot_id'] = boot_id()
    transaction['phase'] = 'awaiting-reboot'
    atomic_json(STATE / 'reboot-pending.json', {'boot_id': boot_id()})
    atomic_json(STATE / 'transaction.json', transaction)


def reboot_if_needed(config):
    pending = STATE / 'reboot-pending.json'
    if pending.exists() and json.loads(pending.read_text())['boot_id'] != boot_id():
        pending.unlink()
    if pending.exists() or Path('/var/run/reboot-required').exists():
        if config['automatic_reboot']:
            log('Requesting reboot after successful maintenance.')
            run(['systemctl', '--no-block', 'reboot'])
        else:
            log('Reboot required; automatic_reboot is disabled.')


def update(check=False):
    config = {'automatic_major': True, 'automatic_reboot': True}
    if CONFIG.exists():
        custom = json.loads(CONFIG.read_text())
        if not isinstance(custom, dict) or set(custom) - set(config) or any(type(v) is not bool for v in custom.values()):
            raise RuntimeError('Config accepts only boolean automatic_major and automatic_reboot.')
        config.update(custom)
    current = os_release()
    log(f'Installed release: {current[0]} ({current[1]}).')
    pending = STATE / 'reboot-pending.json'
    if not check and pending.exists() and json.loads(pending.read_text())['boot_id'] != boot_id():
        pending.unlink()
    transaction_path = STATE / 'transaction.json'
    transaction = json.loads(transaction_path.read_text()) if transaction_path.exists() else None
    if transaction and transaction['phase'] == 'awaiting-reboot':
        healthy()
        if boot_id() == transaction['boot_id']:
            if not check:
                reboot_if_needed(config)
            return
        if current[0] != transaction['target']:
            raise RuntimeError('Booted release does not match completed transaction.')
        log('Major upgrade confirmed after reboot.')
        if not check:
            os.replace(transaction_path, STATE / 'last-major-upgrade.json')
        transaction = None
    if transaction:
        if check:
            log('Interrupted major upgrade pending; next run resumes target ' + transaction['target'])
            return
        perform_major(transaction)
        reboot_if_needed(config)
        return

    files = source_files()
    # Validate even regular runs so floating Debian suites cannot bypass the release checks.
    migrate_sources(files, current[0], current[0])
    if check:
        probe(files, simulate=True, expected_release=current[0])
        if config['automatic_major']:
            target = target_release(current, stable_release())
            if target:
                log('Would migrate to ' + target)
                probe(migrate_sources(files, current[0], target), simulate=True, expected_release=target)
            else:
                log('Already on latest stable major release.')
        return

    before_path = STATE / 'package-baseline.json'
    if not before_path.exists():
        validate_space()
        atomic_json(before_path, {'fingerprint': fingerprint()})
    # Reconfigure interrupted dpkg work before trying further upgrades.
    recover_dpkg()
    apt('update', '--allow-releaseinfo-change-suite')
    apt('-y', '-f', 'install')
    apt('-y', 'dist-upgrade')
    healthy()
    changed = json.loads(before_path.read_text())['fingerprint'] != fingerprint()
    if changed:
        atomic_json(STATE / 'reboot-pending.json', {'boot_id': boot_id()})
    before_path.unlink()
    atomic_json(STATE / 'last-success.json', {'release': current[0], 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                                             'packages_changed': changed})
    # Boot updated current-release kernel before crossing the release boundary.
    if changed or (STATE / 'reboot-pending.json').exists() or Path('/var/run/reboot-required').exists():
        reboot_if_needed(config)
        return
    if config['automatic_major']:
        target = target_release(current, stable_release())
        if target:
            validate_space(major=True)
            if run(['apt-mark', 'showhold'], capture=True).strip():
                raise RuntimeError('Held packages prevent automatic major upgrade.')
            if (APT / 'preferences').exists() or any((APT / 'preferences.d').glob('*')):
                raise RuntimeError('Remove or review APT pinning before a major upgrade.')
            migrated = migrate_sources(source_files(), current[0], target)
            probe(migrated, simulate=True, expected_release=target)
            transaction = {'phase': 'upgrading', 'source': current[0], 'target': target,
                           'boot_id': boot_id(), 'backup': snapshot(), 'sources': migrated}
            atomic_json(transaction_path, transaction)
            atomic_json(STATE / 'reboot-pending.json', {'boot_id': boot_id()})
            perform_major(transaction)
        else:
            log('Already on latest stable major release.')
    reboot_if_needed(config)
    log('Maintenance complete.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Preview using isolated APT indexes; do not install or reboot')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root (sudo).')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('Another updater is running; skipping.')
            return
        try:
            update(args.check)
        except Exception as error:
            log(f'FAILED: {error}. See journalctl -u trail-camera-update.service.')
            sys.exit(1)


if __name__ == '__main__':
    main()
