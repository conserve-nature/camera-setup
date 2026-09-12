import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('configure_imx500', Path(__file__).parents[1] / 'configure-imx500.py')
camera = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(camera)


class CameraConfigurationTests(unittest.TestCase):
    def test_preserves_comments_and_unrelated_settings_with_final_all_block(self):
        original = '# My camera\ncamera_auto_detect=1\n[pi5]\ndtoverlay=vc4-kms-v3d\n'
        result = camera.transform(original)
        self.assertTrue(result.startswith(original))
        self.assertTrue(result.endswith(camera.BLOCK))
        self.assertEqual(result.count('dtoverlay=imx500'), 1)
        self.assertEqual(camera.transform(result), result)

    def test_existing_managed_block_is_replaced_and_moved_after_user_settings(self):
        original = '# before\n' + camera.BLOCK + '\n[pi5]\ndtparam=pciex1\n'
        result = camera.transform(original)
        self.assertIn('# before\n', result)
        self.assertIn('[pi5]\ndtparam=pciex1\n', result)
        self.assertEqual(result.count(camera.BEGIN), 1)
        self.assertTrue(result.endswith(camera.BLOCK))
        self.assertEqual(camera.transform(result), result)

    def test_existing_plain_imx500_is_preserved_as_comment_to_prevent_duplicate_load(self):
        result = camera.transform('[all]\ndtoverlay=imx500 # AI camera\n')
        self.assertIn('# Moved into managed IMX500 block: dtoverlay=imx500 # AI camera\n', result)
        self.assertEqual([line for line in result.splitlines() if line.startswith('dtoverlay=')],
                         ['dtoverlay=imx500'])
        self.assertEqual(camera.transform(result), result)

    def test_other_explicit_camera_overlays_and_parameters_fail_for_review(self):
        for overlay in ('imx219', 'imx477,cam0', 'ov5647', 'arducam-pivariety',
                        'camera-mux-4port', 'imx500,cam0'):
            with self.subTest(overlay=overlay), self.assertRaisesRegex(ValueError, 'requires review'):
                camera.transform(f'dtoverlay={overlay}\n')

    def test_commented_camera_overlay_is_preserved(self):
        original = '# dtoverlay=imx219\n'
        self.assertTrue(camera.transform(original).startswith(original))

    def test_malformed_or_multiple_managed_blocks_rejected(self):
        for original in (camera.BEGIN + '\n', camera.END + '\n', camera.BLOCK * 2,
                         camera.END + '\n' + camera.BEGIN + '\n'):
            with self.subTest(original=original), self.assertRaisesRegex(ValueError, 'Malformed'):
                camera.transform(original)

    def test_fixture_backup_reboot_marker_and_second_run_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, state = root / 'config.txt', root / 'state'
            original = '# user configuration\n[all]\ncamera_auto_detect=1\n'
            config.write_text(original)
            self.assertTrue(camera.configure(config, state, 'boot-a'))
            backup = config.with_name(config.name + '.trail-camera-imx500.bak')
            self.assertEqual(backup.read_text(), original)
            self.assertEqual(json.loads((state / 'reboot-pending.json').read_text()), {'boot_id': 'boot-a'})
            modified_time = config.stat().st_mtime_ns
            self.assertFalse(camera.configure(config, state, 'boot-b'))
            self.assertEqual(config.stat().st_mtime_ns, modified_time)
            self.assertEqual(backup.read_text(), original)
            self.assertEqual(json.loads((state / 'reboot-pending.json').read_text()), {'boot_id': 'boot-a'})
            config.write_text(config.read_text() + '\n[pi5]\ndtparam=pciex1\n')
            self.assertTrue(camera.configure(config, state, 'boot-b'))
            self.assertEqual(backup.read_text(), original)

    def test_check_changes_no_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.txt'
            config.write_text('# original\n')
            self.assertTrue(camera.configure(config, root / 'state', '', check=True))
            self.assertEqual(config.read_text(), '# original\n')
            self.assertEqual(list(root.iterdir()), [config])

    def test_reboot_marker_is_durable_before_config_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, state = root / 'config.txt', root / 'state'
            config.write_text('# original\n')
            real_write = camera.atomic_write

            def interrupted_write(path, content):
                if path == config:
                    self.assertEqual(json.loads((state / 'reboot-pending.json').read_text()), {'boot_id': 'boot-a'})
                    raise OSError('simulated write failure')
                real_write(path, content)

            with patch.object(camera, 'atomic_write', side_effect=interrupted_write):
                with self.assertRaisesRegex(OSError, 'simulated'):
                    camera.configure(config, state, 'boot-a')
            self.assertEqual(config.read_text(), '# original\n')
            self.assertTrue(camera.configure(config, state, 'boot-a'))


if __name__ == '__main__':
    unittest.main()
