"""Fixture regressions for offline networking; never alter live interfaces."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location('configure_network', Path(__file__).parents[1] / 'configure-network.py')
network = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(network)


class NetworkConfigurationTests(unittest.TestCase):
    def test_hosts_resolves_local_names_in_both_families_without_losing_aliases(self):
        original = ('# local names\n127.0.0.1 localhost retained-alias # keep comment\n'
                    '192.168.1.4 ipaw old-alias\n::1 ip6-localhost ip6-loopback\n'
                    '192.0.2.5 other-host # unrelated\n')
        result = network.hosts_text(original, 'ipaw')
        self.assertIn('127.0.0.1\tlocalhost\n', result)
        self.assertIn('127.0.1.1\tipaw\n', result)
        self.assertIn('::1\tlocalhost ip6-localhost ip6-loopback ipaw\n', result)
        self.assertIn('retained-alias', result)
        self.assertIn('# keep comment', result)
        self.assertIn('192.168.1.4\told-alias', result)
        self.assertIn('192.0.2.5 other-host # unrelated\n', result)
        self.assertEqual(network.hosts_text(result, 'ipaw'), result)

    def test_fqdn_and_short_hostname_are_local(self):
        result = network.hosts_text('', 'ipaw.example.test')
        self.assertIn('127.0.1.1\tipaw.example.test ipaw\n', result)
        self.assertIn('::1\tlocalhost ip6-localhost ip6-loopback ipaw.example.test ipaw\n', result)

    def test_existing_localhost_localdomain_alias_is_retained_on_loopback(self):
        original = '127.0.0.1 localhost localhost.localdomain\n'
        result = network.hosts_text(original, 'ipaw')
        self.assertIn('127.0.0.1\tlocalhost localhost.localdomain\n', result)
        self.assertIn('::1\tlocalhost localhost.localdomain ip6-localhost ip6-loopback ipaw\n', result)
        self.assertEqual(network.hosts_text(result, 'ipaw'), result)

    def test_old_managed_hostname_is_removed_when_hostname_changes(self):
        first = network.hosts_text('# keep\n', 'old-camera')
        second = network.hosts_text(first, 'new-camera')
        self.assertNotIn('old-camera', second)
        self.assertIn('new-camera', second)
        self.assertIn('# keep\n', second)

    def test_hostname_cannot_inject_configuration(self):
        for hostname in ('', 'camera\n192.0.2.5 evil', 'camera # comment', '-camera'):
            with self.subTest(hostname=hostname), self.assertRaises(ValueError):
                network.hosts_text('', hostname)

    def test_nss_uses_files_before_network_resolvers(self):
        original = 'passwd: files systemd\nhosts: resolve [!UNAVAIL=return] files mdns4_minimal dns\n'
        result = network.nss_text(original)
        self.assertEqual(result, 'passwd: files systemd\nhosts: files resolve [!UNAVAIL=return] mdns4_minimal dns\n')
        self.assertEqual(network.nss_text(result), result)

    def test_nss_adds_missing_hosts_and_preserves_comments(self):
        original = '# configuration\npasswd: files\n'
        self.assertEqual(network.nss_text(original), original + 'hosts: files dns\n')

    def test_existing_files_first_nss_is_untouched(self):
        original = 'hosts:          files mdns4_minimal [NOTFOUND=return] dns # keep\n'
        self.assertEqual(network.nss_text(original), original)

    def test_nss_removes_files_action_override_only(self):
        result = network.nss_text('hosts: dns [NOTFOUND=return] files [SUCCESS=continue]\n')
        self.assertEqual(result, 'hosts: files dns [NOTFOUND=return]\n')

    def fixture(self, root, command_line='console=serial0,115200'):
        for name, content in {'etc/hostname': 'ipaw\n', 'etc/hosts': '# original\n',
                              'etc/nsswitch.conf': 'hosts: dns files\n',
                              'proc/cmdline': command_line}.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def test_persistent_fixture_is_idempotent_and_backs_up_original_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            self.assertTrue(network.configure(root))
            snapshot = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(network.configure(root), [])
            self.assertEqual({str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}, snapshot)
            self.assertEqual((root / 'etc/hosts.trail-camera-network.bak').read_text(), '# original\n')
            (root / 'etc/hosts').write_text('# edited\n')
            network.configure(root)
            self.assertEqual((root / 'etc/hosts.trail-camera-network.bak').read_text(), '# original\n')

    def test_service_touches_only_loopback_and_does_not_wait_for_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            network.configure(root)
            service = (root / 'etc/systemd/system/trail-camera-loopback.service').read_text()
            self.assertIn('DefaultDependencies=no', service)
            self.assertNotIn('network-online', service)
            commands = [line for line in service.splitlines() if line.startswith('ExecStart=')]
            self.assertEqual(commands, ['ExecStart=/usr/sbin/sysctl -w net.ipv6.conf.lo.disable_ipv6=0',
                                        'ExecStart=/usr/sbin/ip link set dev lo up',
                                        'ExecStart=/usr/sbin/ip address replace 127.0.0.1/8 dev lo',
                                        'ExecStart=/usr/sbin/ip -6 address replace ::1/128 dev lo'])
            nm = (root / 'etc/NetworkManager/conf.d/90-trail-camera-loopback.conf').read_text()
            self.assertIn('match-device=interface-name:lo\nmanaged=0', nm)
            sysctl = (root / 'etc/sysctl.d/90-trail-camera-ipv6.conf').read_text()
            for scope in ('default', 'lo'):
                self.assertIn(f'net.ipv6.conf.{scope}.disable_ipv6=0', sysctl)
            self.assertNotIn('conf.all', sysctl)
            self.assertIn('manage_etc_hosts: false', (root / 'etc/cloud/cloud.cfg.d/99-trail-camera-hosts.cfg').read_text())

    def test_cloud_template_preserves_dynamic_hostname_placeholders(self):
        for variables in ('$fqdn $hostname', '{{fqdn}} {{hostname}}'):
            with self.subTest(variables=variables), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root)
                template = root / 'etc/cloud/templates/hosts.debian.tmpl'
                template.parent.mkdir(parents=True)
                template.write_text(f'## template:jinja\n127.0.1.1 {variables}\n127.0.0.1 localhost\n')
                network.configure(root)
                self.assertIn('## template:jinja\n', template.read_text())
                self.assertIn(f'127.0.1.1\t{variables}\n', template.read_text())
                self.assertIn(f'::1\tlocalhost ip6-localhost ip6-loopback {variables}\n', template.read_text())
                self.assertEqual(network.configure(root), [])

    def test_kernel_ipv6_disable_requires_explicit_future_boot_fix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, 'console=tty1 ipv6.disable=1')
            with self.assertRaisesRegex(ValueError, 'ipv6.disable=1'):
                network.configure(root)
            self.assertFalse((root / 'etc/systemd/system/trail-camera-loopback.service').exists())

    def test_check_mode_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertTrue(network.configure(root, check=True))
            self.assertEqual({str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
