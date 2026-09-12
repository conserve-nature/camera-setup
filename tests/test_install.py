"""Exercise bootstrap/orchestration with fake host commands; never change the host."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\n')
if os.environ.get('FAIL_COMMAND') == name:
    sys.exit(17)
if name == 'id':
    print(os.environ.get('MOCK_UID', '0'))
elif name == 'stat':
    print(os.environ.get('PATH_PERMISSIONS', '0 755'))
elif name == 'apt-get':
    with open(os.environ['APT_ENV_LOG'], 'a') as log:
        log.write(json.dumps({key: os.environ.get(key) for key in (
            'DEBIAN_FRONTEND', 'APT_LISTCHANGES_FRONTEND', 'NEEDRESTART_MODE',
            'UCF_FORCE_CONFFOLD')}) + '\n')
elif name == 'dpkg':
    print('amd64')
elif name == 'dpkg-query':
    print('install ok installed' if os.environ.get('PACKAGES_PRESENT') else 'unknown')
elif name == 'sudo':
    os.environ['MOCK_UID'] = '0'
    sys.exit(subprocess.call(args, env=os.environ))
elif name == 'install':
    pathlib.Path(args[-1]).mkdir(parents=True, exist_ok=True)
elif name == 'git':
    action = args[2] if args[:1] == ['-C'] else args[0]
    if action == 'clone':
        target = pathlib.Path(args[-1]); (target / '.git').mkdir(parents=True)
        (target / 'setup.sh').write_text('#!/bin/bash\nprintf "setup\\n" >> "$SETUP_LOG"\n')
    elif action == 'rev-parse':
        print('true')
    elif action == 'remote':
        print(os.environ.get('ORIGIN', 'https://github.com/conserve-nature/camera-setup.git'))
    elif action == 'symbolic-ref':
        print(os.environ.get('BRANCH', 'main'))
    elif action == 'status':
        print(os.environ.get('DIRTY', ''), end='')
    elif action == 'merge-base':
        sys.exit(1 if os.environ.get('DIVERGED') else 0)
    elif action == 'config':
        sys.exit(1)
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.bin = self.root / 'bin'; self.bin.mkdir()
        for name in ('id', 'stat', 'dpkg', 'dpkg-query', 'sudo', 'install', 'git', 'apt-get'):
            path = self.bin / name; path.write_text(MOCK); path.chmod(0o755)
        self.target = self.root / 'checkout'
        self.log = self.root / 'calls.jsonl'
        self.setup_log = self.root / 'setup.log'
        self.os_release = self.root / 'os-release'; self.os_release.write_text('ID=debian\n')
        self.env = dict(os.environ, PATH=f'{self.bin}:/usr/bin:/bin', CALL_LOG=str(self.log),
                        SETUP_LOG=str(self.setup_log), APT_ENV_LOG=str(self.root / 'apt-env.jsonl'))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_install(self, **env):
        script = (ROOT / 'install.sh').read_text().replace(
            '/opt/trail-camera/camera-setup', str(self.target)).replace(
            '/etc/os-release', str(self.os_release))
        return subprocess.run(['/bin/bash', '-c', script], env=dict(self.env, **env),
                              text=True, capture_output=True)

    def existing_checkout(self):
        self.target.mkdir(); (self.target / '.git').mkdir()
        (self.target / 'setup.sh').write_text('printf "setup\\n" >> "$SETUP_LOG"\n')

    def test_fresh_install_installs_prerequisites_clones_then_sets_up(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any(c[:2] == ['apt-get', 'update'] for c in calls))
        install = next(c for c in calls if c[:3] == ['apt-get', 'install', '-y'])
        for package in ('git', 'python3', 'gpgv', 'debian-archive-keyring', 'iproute2', 'procps'):
            self.assertIn(package, install)
        self.assertTrue(any(c[:2] == ['git', 'clone'] for c in calls))
        self.assertEqual(self.setup_log.read_text(), 'setup\n')
        self.assertFalse(any(c[0] in ('reboot', 'systemctl') or 'upgrade' in c for c in calls))

    def test_apt_is_bounded_noninteractive_and_does_not_restart_services(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        apt_calls = [c for c in self.calls() if c[0] == 'apt-get']
        self.assertEqual(len(apt_calls), 2)
        for call in apt_calls:
            for option in ('DPkg::Lock::Timeout=600', 'Acquire::Retries=3',
                           'Acquire::http::Timeout=60', 'Acquire::https::Timeout=60',
                           'APT::Update::Error-Mode=any'):
                self.assertIn(option, call)
        apt_envs = [json.loads(line) for line in
                    (self.root / 'apt-env.jsonl').read_text().splitlines()]
        self.assertEqual(apt_envs, [{
            'DEBIAN_FRONTEND': 'noninteractive', 'APT_LISTCHANGES_FRONTEND': 'none',
            'NEEDRESTART_MODE': 'l', 'UCF_FORCE_CONFFOLD': '1',
        }] * 2)

    def test_existing_prerequisites_skip_apt_entirely(self):
        result = self.run_install(PACKAGES_PRESENT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] == 'apt-get' for c in self.calls()))

    def test_bash_c_escalates_without_a_script_file(self):
        result = self.run_install(MOCK_UID='1000', PACKAGES_PRESENT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(c[0] == 'sudo' for c in self.calls()))
        self.assertTrue(self.setup_log.exists())

    def test_clean_rerun_fast_forwards_then_sets_up(self):
        self.existing_checkout()
        result = self.run_install(PACKAGES_PRESENT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any('fetch' in c for c in calls))
        self.assertTrue(any('merge' in c and '--ff-only' in c for c in calls))
        self.assertTrue(self.setup_log.exists())

    def test_unsafe_checkouts_refuse_setup(self):
        for state in ({'DIRTY': ' M setup.sh'}, {'BRANCH': 'feature'},
                      {'ORIGIN': 'https://example.org/other.git'}, {'DIVERGED': '1'}):
            with self.subTest(state=state):
                if not self.target.exists(): self.existing_checkout()
                result = self.run_install(PACKAGES_PRESENT='1', **state)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.setup_log.exists())

    def test_unsafe_path_permissions_refused(self):
        for permissions in ('1000 755', '0 775', '0 777'):
            with self.subTest(permissions=permissions):
                result = self.run_install(PATH_PERMISSIONS=permissions)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.setup_log.exists())

    def test_prerequisite_failure_stops_before_clone(self):
        result = self.run_install(FAIL_COMMAND='apt-get')
        self.assertEqual(result.returncode, 17)
        self.assertFalse(any(c[0] == 'git' for c in self.calls()))
        self.assertFalse(self.setup_log.exists())

    def test_git_failure_does_not_apply_setup(self):
        self.existing_checkout()
        result = self.run_install(PACKAGES_PRESENT='1', FAIL_COMMAND='git')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.setup_log.exists())

    def test_non_git_target_refused(self):
        self.target.mkdir()
        result = self.run_install(PACKAGES_PRESENT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.setup_log.exists())

    def test_symlink_target_refused(self):
        destination = self.root / 'elsewhere'; destination.mkdir()
        self.target.symlink_to(destination, target_is_directory=True)
        result = self.run_install(PACKAGES_PRESENT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.setup_log.exists())

    def test_non_debian_refused_before_apt_or_git(self):
        self.os_release.write_text('ID=fedora\n')
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] in ('apt-get', 'git') for c in self.calls()))


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.log = self.root / 'setup.log'
        self.bin = self.root / 'bin'; self.bin.mkdir()
        uid = self.bin / 'id'; uid.write_text('#!/bin/bash\necho 0\n'); uid.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.bin}:/usr/bin:/bin', SETUP_LOG=str(self.log))
        (self.root / 'setup.sh').write_text((ROOT / 'setup.sh').read_text())
        for name in ('01-setup-updates.sh', '02-setup-network.sh'):
            (self.root / name).write_text(f'printf "{name}\\n" >> "$SETUP_LOG"\n')
        for name in ('update-os.py', 'configure-imx500.py', 'configure-network.py'):
            (self.root / name).write_text('# payload\n')

    def run_setup(self):
        return subprocess.run(['/bin/bash', str(self.root / 'setup.sh')], env=self.env,
                              text=True, capture_output=True)

    def test_offline_setup_uses_explicit_order(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(),
                         ['01-setup-updates.sh', '02-setup-network.sh'])

    def test_preflight_missing_later_script_before_first_mutation(self):
        (self.root / '02-setup-network.sh').unlink()
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_preflight_missing_payload_before_first_mutation(self):
        (self.root / 'update-os.py').unlink()
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_failed_first_step_stops_second(self):
        (self.root / '01-setup-updates.sh').write_text('exit 17\n')
        self.assertEqual(self.run_setup().returncode, 17)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
