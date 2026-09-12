"""Safety and recovery tests; never invoke the host's package manager."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location('update_os', Path(__file__).parents[1] / 'update-os.py')
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)

DEBIAN = 'deb https://deb.debian.org/debian bookworm main non-free-firmware\n'


class SourceMigrationTests(unittest.TestCase):
    def migrate(self, text, filename='sources.list'):
        return updater.migrate_sources({filename: text}, 'bookworm', 'trixie')[filename]

    def test_legacy_preserves_options_comments_and_missing_final_newline(self):
        text = ('# bookworm in comments is not a suite\n'
                'deb [arch=arm64 signed-by=/etc/apt/keyrings/debian.gpg] '
                'https://deb.debian.org/debian bookworm main # bookworm\n'
                'deb-src http://security.debian.org/debian-security bookworm-security main')
        expected = text.replace('/debian bookworm main', '/debian trixie main').replace(
            '/debian-security bookworm-security', '/debian-security trixie-security')
        self.assertEqual(self.migrate(text), expected)

    def test_deb822_multiline_suites_and_multiple_uris(self):
        text = ('Types: deb deb-src\nURIs: https://deb.debian.org/debian\n'
                ' http://deb.debian.org/debian\nSuites: bookworm\n'
                ' bookworm-updates bookworm-backports\nComponents: main non-free-firmware\n'
                'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n')
        result = self.migrate(text, 'sources.list.d/debian.sources')
        self.assertIn('Suites: trixie trixie-updates trixie-backports\nComponents:', result)
        self.assertIn(' http://deb.debian.org/debian\n', result)
        self.assertIn('Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n', result)

    def test_disabled_deb822_unknown_repository_is_preserved(self):
        disabled = ('Enabled: no\nTypes: deb\nURIs: https://example.com/packages\n'
                    'Suites: old-version\nComponents: main\n')
        files = {'sources.list': DEBIAN, 'sources.list.d/disabled.sources': disabled}
        result = updater.migrate_sources(files, 'bookworm', 'trixie')
        self.assertEqual(result['sources.list.d/disabled.sources'], disabled)

    def test_vendor_docker_and_raspberry_pi_sources(self):
        text = (DEBIAN + 'deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] '
                'https://download.docker.com/linux/debian bookworm stable\n'
                'deb http://archive.raspberrypi.com/debian bookworm main\n')
        result = self.migrate(text)
        self.assertNotIn('bookworm', result)
        self.assertIn('https://download.docker.com/linux/debian trixie stable', result)
        self.assertIn('http://archive.raspberrypi.com/debian trixie main', result)

    def test_comments_and_blank_lines_do_not_count_as_enabled_sources(self):
        with self.assertRaisesRegex(RuntimeError, 'No enabled'):
            self.migrate('# deb https://deb.debian.org/debian bookworm main\n\n')

    def test_only_disabled_deb822_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'No enabled'):
            self.migrate('Enabled: no\nTypes: deb\nURIs: https://example.com\n'
                         'Suites: bookworm\n', 'disabled.sources')

    def test_unknown_or_confusing_uri_is_rejected(self):
        for uri in ('https://example.com/debian', 'https://deb.debian.org.evil.test/debian',
                    'file:/debian', 'https://user@deb.debian.org/debian',
                    'https://deb.debian.org/debian?mirror=evil',
                    'https://deb.debian.org/debian#fragment'):
            with self.subTest(uri=uri), self.assertRaisesRegex(RuntimeError, 'Unsupported APT archive'):
                self.migrate(f'deb {uri} bookworm main\n')

    def test_floating_mixed_and_unsupported_suites_rejected(self):
        for suite in ('stable', 'oldstable', 'testing', 'trixie', 'bullseye', 'bookworm-proposed-updates'):
            with self.subTest(suite=suite), self.assertRaisesRegex(RuntimeError, 'installed codename'):
                self.migrate(DEBIAN + f'deb https://deb.debian.org/debian {suite} main\n')

    def test_deb822_mixed_suites_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'installed codename'):
            self.migrate('Types: deb\nURIs: https://deb.debian.org/debian\n'
                         'Suites: bookworm trixie\nComponents: main\n', 'debian.sources')

    def test_deb822_duplicate_fields_rejected_case_insensitively(self):
        with self.assertRaisesRegex(RuntimeError, 'Duplicate source field'):
            self.migrate('Types: deb\nURIs: https://deb.debian.org/debian\n'
                         'Suites: bookworm\nsuites: trixie\n', 'debian.sources')

    def test_deb822_insecure_options_rejected(self):
        for option in ('Trusted', 'Allow-Insecure', 'Allow-Weak', 'Allow-Downgrade-To-Insecure'):
            for value in ('yes', 'true', '1'):
                with self.subTest(option=option, value=value), self.assertRaisesRegex(RuntimeError, 'Insecure'):
                    self.migrate('Types: deb\nURIs: https://deb.debian.org/debian\n'
                                 f'Suites: bookworm\n{option}: {value}\n', 'debian.sources')

    def test_legacy_insecure_options_rejected(self):
        for option in ('trusted', 'allow-insecure', 'allow-weak', 'allow-downgrade-to-insecure'):
            for value in ('yes', 'true', '1'):
                with self.subTest(option=option, value=value), self.assertRaisesRegex(RuntimeError, 'Insecure'):
                    self.migrate(f'deb [{option}={value}] https://deb.debian.org/debian bookworm main\n')

    def test_source_files_ignores_backups_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sources.list.d').mkdir()
            for name in ('sources.list', 'sources.list.d/debian.sources',
                         'sources.list.d/docker.list', 'sources.list.d/old.list.save',
                         'sources.list.d/notes.txt'):
                (root / name).write_text(DEBIAN)
            self.assertEqual(set(updater.source_files(root)),
                             {'sources.list', 'sources.list.d/debian.sources', 'sources.list.d/docker.list'})


class ReleaseTargetTests(unittest.TestCase):
    def test_same_release_is_noop(self):
        self.assertIsNone(updater.target_release(('trixie', 13), ('trixie', 13)))

    def test_adjacent_release_is_selected(self):
        self.assertEqual(updater.target_release(('bookworm', 12), ('trixie', 13)), 'trixie')

    def test_skip_downgrade_and_conflicting_identity_rejected(self):
        for stable, reason in ((('forky', 14), 'adjacent'), (('bullseye', 11), 'downgrade'),
                               (('trixie', 12), 'Conflicting')):
            with self.subTest(stable=stable), self.assertRaisesRegex(RuntimeError, reason):
                updater.target_release(('bookworm', 12), stable)


class StableDiscoveryTests(unittest.TestCase):
    VALID = 'Origin: Debian\nSuite: stable\nCodename: trixie\nVersion: 13.2\n'

    def discover(self, metadata):
        def verify(args, **kwargs):
            self.assertEqual(args[0], 'gpgv')
            self.assertEqual(Path(args[-1]).read_bytes(), b'signed payload')
            Path(args[args.index('--output') + 1]).write_text(metadata)

        with patch.object(Path, 'exists', return_value=True), \
                patch.object(updater.urllib.request, 'urlopen', return_value=io.BytesIO(b'signed payload')), \
                patch.object(updater, 'run', side_effect=verify):
            return updater.stable_release()

    def test_authenticated_stable_metadata_returns_major_identity(self):
        self.assertEqual(self.discover(self.VALID), ('trixie', 13))

    def test_signature_failure_is_fatal(self):
        with patch.object(Path, 'exists', return_value=True), \
                patch.object(updater.urllib.request, 'urlopen', return_value=io.BytesIO(self.VALID.encode())), \
                patch.object(updater, 'run', side_effect=subprocess.CalledProcessError(2, ['gpgv'])) as verify:
            with self.assertRaises(subprocess.CalledProcessError):
                updater.stable_release()
        self.assertEqual(verify.call_count, 1)

    def test_missing_archive_keyring_fails_before_network_access(self):
        with patch.object(Path, 'exists', return_value=False), \
                patch.object(updater.urllib.request, 'urlopen') as fetch:
            with self.assertRaisesRegex(RuntimeError, 'debian-archive-keyring'):
                updater.stable_release()
        fetch.assert_not_called()

    def test_other_origin_or_suite_rejected_even_after_signature_success(self):
        for metadata in (self.VALID.replace('Origin: Debian', 'Origin: Other'),
                         self.VALID.replace('Suite: stable', 'Suite: testing')):
            with self.subTest(metadata=metadata), self.assertRaisesRegex(RuntimeError, 'not Debian stable'):
                self.discover(metadata)

    def test_invalid_or_missing_release_identity_rejected(self):
        for metadata in (self.VALID.replace('Codename: trixie', 'Codename: trixie/../../evil'),
                         self.VALID.replace('Version: 13.2', 'Version: thirteen'),
                         self.VALID.replace('Codename: trixie\n', ''),
                         self.VALID.replace('Version: 13.2\n', '')):
            with self.subTest(metadata=metadata), self.assertRaisesRegex(RuntimeError, 'Invalid stable release identity'):
                self.discover(metadata)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.fetched_metadata = []
        self.temporary_roots = []

        def fake_apt(*args, extra=()):
            options = dict(option.split('=', 1) for option in extra if option != '-o')
            root = Path(options['Dir::State::lists']).parent
            self.temporary_roots.append(root)
            self.assertEqual((root / 'sources.list').read_text(), DEBIAN)
            self.assertNotEqual(options['Dir::Etc::sourcelist'], '/etc/apt/sources.list')
            if args == ('update',):
                for index, metadata in enumerate(self.fetched_metadata):
                    (root / 'lists' / f'archive-{index}_InRelease').write_text(metadata)

        for name, mock in [('apt', Mock(side_effect=fake_apt)),
                           ('run', Mock(return_value='arm64\n'))]:
            patcher = patch.object(updater, name, mock)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def probe(self):
        updater.probe({'sources.list': DEBIAN}, simulate=True, expected_release='trixie')

    def test_matching_authenticated_releases_are_simulated_in_isolated_indexes(self):
        self.fetched_metadata = [f'Codename: {suite}\nArchitectures: amd64 arm64 armhf\n'
                                 for suite in ('trixie', 'trixie-updates', 'trixie-security', 'trixie-backports')]
        self.probe()
        self.assertEqual([call.args for call in self.apt.call_args_list], [('update',), ('-s', 'dist-upgrade')])
        self.run.assert_called_once_with(['dpkg', '--print-architecture'], capture=True)
        self.assertTrue(all(not root.exists() for root in self.temporary_roots))

    def test_wrong_codename_from_any_archive_prevents_simulation(self):
        self.fetched_metadata = ['Codename: trixie\nArchitectures: arm64\n',
                                 'Codename: bookworm\nArchitectures: arm64\n']
        with self.assertRaisesRegex(RuntimeError, 'expected release/architecture'):
            self.probe()
        self.assertEqual([call.args for call in self.apt.call_args_list], [('update',)])

    def test_native_architecture_must_be_published(self):
        for architectures in ('amd64 armhf', '', 'arm64-extra'):
            with self.subTest(architectures=architectures):
                self.fetched_metadata = [f'Codename: trixie\nArchitectures: {architectures}\n']
                self.apt.reset_mock()
                with self.assertRaisesRegex(RuntimeError, 'expected release/architecture'):
                    self.probe()
                self.assertEqual([call.args for call in self.apt.call_args_list], [('update',)])

    def test_no_authenticated_indexes_prevents_simulation(self):
        with self.assertRaisesRegex(RuntimeError, 'No authenticated InRelease'):
            self.probe()
        self.assertEqual([call.args for call in self.apt.call_args_list], [('update',)])

    def test_apt_authentication_failure_prevents_simulation(self):
        self.apt.side_effect = subprocess.CalledProcessError(100, ['apt-get', 'update'])
        with self.assertRaises(subprocess.CalledProcessError):
            self.probe()
        self.assertEqual([call.args for call in self.apt.call_args_list], [('update',)])
        self.run.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.apt_root = self.root / 'apt'
        self.apt_root.mkdir()
        for name, value in [('STATE', self.state), ('APT', self.apt_root),
                            ('CONFIG', self.root / 'config.json')]:
            patcher = patch.object(updater, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks = {}
        for name, value in [('run', ''), ('apt', None), ('healthy', None), ('validate_space', None),
                            ('os_release', ('bookworm', 12)), ('boot_id', 'boot-a'),
                            ('source_files', {'sources.list': DEBIAN}),
                            ('fingerprint', 'same'), ('stable_release', ('bookworm', 12)),
                            ('probe', None), ('snapshot', '/saved/backup'), ('log', None)]:
            patcher = patch.object(updater, name, Mock(return_value=value))
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def write_state(self, name, data):
        path = self.state / name
        path.write_text(json.dumps(data))
        return path

    def transaction(self, phase='upgrading'):
        return {'phase': phase, 'source': 'bookworm', 'target': 'trixie',
                'boot_id': 'boot-a', 'sources': {'sources.list': DEBIAN.replace('bookworm', 'trixie')}}

    def test_interrupted_major_resumes_saved_target_without_rediscovery(self):
        transaction = self.transaction()
        self.write_state('transaction.json', transaction)
        with patch.object(updater, 'perform_major') as perform, patch.object(updater, 'reboot_if_needed') as reboot:
            updater.update()
        perform.assert_called_once_with(transaction)
        reboot.assert_called_once()
        self.mocks['stable_release'].assert_not_called()
        self.mocks['source_files'].assert_not_called()

    def test_check_does_not_resume_or_modify_interrupted_major(self):
        path = self.write_state('transaction.json', self.transaction())
        original = path.read_bytes()
        with patch.object(updater, 'perform_major') as perform:
            updater.update(check=True)
        perform.assert_not_called()
        self.mocks['apt'].assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_major_repair_rewrites_all_saved_sources_and_marks_awaiting_reboot(self):
        transaction = self.transaction()
        transaction['sources']['sources.list.d/vendor.list'] = 'saved vendor source\n'
        self.mocks['os_release'].return_value = ('trixie', 13)
        self.mocks['boot_id'].return_value = 'boot-after-interruption'
        updater.perform_major(transaction)
        for name, content in transaction['sources'].items():
            self.assertEqual((self.apt_root / name).read_text(), content)
        self.assertEqual(json.loads((self.state / 'transaction.json').read_text())['phase'], 'awaiting-reboot')
        self.assertEqual(json.loads((self.state / 'transaction.json').read_text())['boot_id'], 'boot-after-interruption')
        self.assertEqual(json.loads((self.state / 'reboot-pending.json').read_text()),
                         {'boot_id': 'boot-after-interruption'})
        self.mocks['healthy'].assert_called_once()

    def test_major_failure_preserves_transaction_for_retry(self):
        transaction = self.transaction()
        path = self.write_state('transaction.json', transaction)
        self.mocks['apt'].side_effect = subprocess.CalledProcessError(100, ['apt-get'])
        with self.assertRaises(subprocess.CalledProcessError):
            updater.perform_major(transaction)
        self.assertEqual(json.loads(path.read_text())['phase'], 'upgrading')

    def test_awaiting_reboot_retries_reboot_without_reinstalling(self):
        self.mocks['os_release'].return_value = ('trixie', 13)
        self.write_state('transaction.json', self.transaction('awaiting-reboot'))
        self.write_state('reboot-pending.json', {'boot_id': 'boot-a'})
        updater.update()
        self.mocks['run'].assert_called_once_with(['systemctl', '--no-block', 'reboot'])
        self.mocks['apt'].assert_not_called()
        self.assertTrue((self.state / 'transaction.json').exists())

    def test_boot_wrong_release_cannot_finalize_transaction(self):
        self.mocks['boot_id'].return_value = 'boot-b'
        path = self.write_state('transaction.json', self.transaction('awaiting-reboot'))
        with self.assertRaisesRegex(RuntimeError, 'Booted release'):
            updater.update()
        self.assertTrue(path.exists())

    def test_reboot_disabled_keeps_pending_marker(self):
        path = self.write_state('reboot-pending.json', {'boot_id': 'boot-a'})
        updater.reboot_if_needed({'automatic_reboot': False})
        self.mocks['run'].assert_not_called()
        self.assertTrue(path.exists())

    def test_failed_package_upgrade_preserves_original_fingerprint(self):
        self.mocks['fingerprint'].return_value = 'before'
        self.mocks['apt'].side_effect = subprocess.CalledProcessError(100, ['apt-get'])
        with self.assertRaises(subprocess.CalledProcessError):
            updater.update()
        self.assertEqual(json.loads((self.state / 'package-baseline.json').read_text()), {'fingerprint': 'before'})
        self.assertFalse((self.state / 'last-success.json').exists())

    def test_recovered_package_change_requests_reboot_before_major_discovery(self):
        self.write_state('package-baseline.json', {'fingerprint': 'before-interruption'})
        self.mocks['fingerprint'].return_value = 'after-recovery'
        updater.update()
        self.assertFalse((self.state / 'package-baseline.json').exists())
        self.assertEqual(json.loads((self.state / 'reboot-pending.json').read_text()), {'boot_id': 'boot-a'})
        self.mocks['stable_release'].assert_not_called()
        self.mocks['run'].assert_any_call(['systemctl', '--no-block', 'reboot'])

    def test_dpkg_configure_failure_still_attempts_dependency_repair(self):
        self.mocks['run'].side_effect = subprocess.CalledProcessError(1, ['dpkg'])
        updater.update()
        self.mocks['apt'].assert_any_call('-y', '-f', 'install')
        self.mocks['healthy'].assert_called_once()

    def test_previous_boot_pending_marker_does_not_defer_release_discovery(self):
        pending = self.write_state('reboot-pending.json', {'boot_id': 'old-boot'})
        updater.update()
        self.assertFalse(pending.exists())
        self.mocks['stable_release'].assert_called_once()

    def test_successful_reboot_finalizes_major_transaction(self):
        self.mocks['boot_id'].return_value = 'boot-b'
        self.mocks['os_release'].return_value = ('trixie', 13)
        self.mocks['stable_release'].return_value = ('trixie', 13)
        self.mocks['source_files'].return_value = {'sources.list': DEBIAN.replace('bookworm', 'trixie')}
        self.write_state('transaction.json', self.transaction('awaiting-reboot'))
        self.write_state('reboot-pending.json', {'boot_id': 'boot-a'})
        updater.update()
        self.assertFalse((self.state / 'transaction.json').exists())
        self.assertFalse((self.state / 'reboot-pending.json').exists())
        self.assertEqual(json.loads((self.state / 'last-major-upgrade.json').read_text())['target'], 'trixie')

    def test_invalid_configuration_fails_before_package_operations(self):
        for config in ({'automatic_major': 'false'}, {'automatic_reboot': 0}, {'unknown': True}, []):
            with self.subTest(config=config):
                updater.CONFIG.write_text(json.dumps(config))
                with self.assertRaisesRegex(RuntimeError, 'Config accepts only boolean'):
                    updater.update()
        self.mocks['apt'].assert_not_called()
        self.mocks['run'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
