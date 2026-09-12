#!/usr/bin/env python3
"""Persist offline loopback and name resolution without changing live networking."""
import argparse
import os
from pathlib import Path
import re
import tempfile

BEGIN = '# BEGIN trail-camera local hosts (managed)'
END = '# END trail-camera local hosts (managed)'
SERVICE = '''[Unit]
Description=Trail camera offline IPv4 and IPv6 loopback
DefaultDependencies=no
After=local-fs.target systemd-sysctl.service
Before=sysinit.target network-pre.target NetworkManager.service systemd-networkd.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/sysctl -w net.ipv6.conf.lo.disable_ipv6=0
ExecStart=/usr/sbin/ip link set dev lo up
ExecStart=/usr/sbin/ip address replace 127.0.0.1/8 dev lo
ExecStart=/usr/sbin/ip -6 address replace ::1/128 dev lo
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
'''


def local_hosts(text, names):
    lines = text.splitlines(keepends=True)
    localhost = 'localhost'
    if any('localhost.localdomain' in line.split('#', 1)[0].split()[1:] for line in lines):
        localhost += ' localhost.localdomain'
    starts = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError('Malformed managed hosts block; review /etc/hosts.')
        del lines[starts[0]:ends[0] + 1]
    canonical = {'localhost', 'localhost.localdomain', 'ip6-localhost', 'ip6-loopback', *names}
    for index, line in enumerate(lines):
        content, separator, comment = line.partition('#')
        fields = content.split()
        if len(fields) < 2 or not canonical.intersection(fields[1:]):
            continue
        aliases = [alias for alias in fields[1:] if alias not in canonical]
        suffix = (' #' + comment.rstrip('\n')) if separator else ''
        lines[index] = ((fields[0] + '\t' + ' '.join(aliases) + suffix) if aliases else suffix.lstrip()) + '\n'
    names = ' '.join(names)
    return (''.join(lines).rstrip('\n') + '\n\n' + BEGIN + '\n'
            '127.0.0.1\t' + localhost + '\n127.0.1.1\t' + names + '\n'
            '::1\t' + localhost + ' ip6-localhost ip6-loopback ' + names + '\n' + END + '\n')


def hosts_text(text, hostname):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', hostname):
        raise ValueError('Invalid /etc/hostname; expected one local hostname.')
    names = list(dict.fromkeys([hostname, hostname.split('.')[0]]))
    return local_hosts(text, names)


def nss_text(text):
    matches = list(re.finditer(r'^hosts:[^\n]*', text, re.M))
    if len(matches) > 1:
        raise ValueError('Multiple hosts entries in /etc/nsswitch.conf require review.')
    if not matches:
        return text.rstrip('\n') + '\nhosts: files dns\n'
    match = matches[0]
    content, separator, comment = match[0].partition('#')
    services = content.split(':', 1)[1].strip()
    if re.match(r'^files(?:\s|$)', services) and not re.match(r'^files\s+\[', services):
        return text
    services = re.sub(r'\bfiles\b(?:\s+\[[^]]+\])*', '', services)
    replacement = 'hosts: files' + (' ' + ' '.join(services.split()) if services.strip() else '')
    if separator:
        replacement += ' #' + comment
    return text[:match.start()] + replacement + text[match.end():]


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(root=Path('/'), check=False):
    command_line = root / 'proc/cmdline'
    if command_line.exists() and 'ipv6.disable=1' in command_line.read_text().split():
        raise ValueError('Remove ipv6.disable=1 from the boot command line and reboot later before enabling IPv6; no live changes made.')
    hostname = (root / 'etc/hostname').read_text().strip()
    hosts, nss = root / 'etc/hosts', root / 'etc/nsswitch.conf'
    files = {'etc/hosts': hosts_text(hosts.read_text() if hosts.exists() else '', hostname),
             'etc/nsswitch.conf': nss_text(nss.read_text() if nss.exists() else ''),
             'etc/systemd/system/trail-camera-loopback.service': SERVICE,
             'etc/NetworkManager/conf.d/90-trail-camera-loopback.conf':
                 '# Applied at next boot; setup does not reload NetworkManager.\n'
                 '[device-trail-camera-loopback]\nmatch-device=interface-name:lo\nmanaged=0\n',
             'etc/sysctl.d/90-trail-camera-ipv6.conf':
                 '# Applied at next boot; preserve external interface state during setup.\n'
                 'net.ipv6.conf.default.disable_ipv6=0\nnet.ipv6.conf.lo.disable_ipv6=0\n',
             'etc/cloud/cloud.cfg.d/99-trail-camera-hosts.cfg':
                 '# Keep local resolution available without external networking.\nmanage_etc_hosts: false\n'}
    template = root / 'etc/cloud/templates/hosts.debian.tmpl'
    if template.exists():
        original = re.sub(r'{{\s*(hostname|fqdn)\s*}}', r'{{\1}}', template.read_text())
        variables = ['$fqdn', '$hostname'] if '$hostname' in original else ['{{fqdn}}', '{{hostname}}']
        files['etc/cloud/templates/hosts.debian.tmpl'] = local_hosts(original, variables)
    changed = []
    for name, content in files.items():
        path = root / name
        if path.exists() and path.read_text() == content:
            continue
        changed.append(name)
        if not check:
            backup = path.with_name(path.name + '.trail-camera-network.bak')
            if path.exists() and not backup.exists():
                atomic_write(backup, path.read_text())
            atomic_write(path, content)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'), help='Filesystem root for fixture checks')
    parser.add_argument('--check', action='store_true', help='List changes without writing files')
    args = parser.parse_args()
    try:
        changed = configure(args.root, args.check)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Network configuration failed: {error}\n')
    print(('Would configure: ' if args.check else 'Configured: ') + ', '.join(changed) if changed else 'Offline network configuration already current.')


if __name__ == '__main__':
    main()
