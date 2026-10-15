"""Updater tests use disposable installations; no downloads or live add-on changes."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('update_install', Path(__file__).resolve().parents[1] / 'tools/update_install.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'Aventurine'
        self.target.mkdir()
        (self.target / '__init__.py').write_text('old installation')
        self.zip = self.root / 'update.zip'
        self.archive()

    def archive(self, extra=None, native=True):
        with zipfile.ZipFile(self.zip, 'w') as z:
            z.writestr('Aventurine/__init__.py', "bl_info = {'version': (3, 2, 1)}\n")
            z.writestr('Aventurine/vendor/wheel.json', json.dumps({'wheel': 'ritoshark-0.1.0-cp39-abi3-win_amd64.whl'}))
            if native:
                z.writestr('Aventurine/vendor/ritoshark/ritoshark.pyd', b'new native module')
            if extra:
                z.writestr(extra, 'unwanted')

    def test_release_requires_packaged_windows_asset(self):
        release = {'tag_name': '3.2.1', 'zipball_url': 'https://example.invalid/source.zip', 'assets': []}
        with self.assertRaisesRegex(ValueError, 'source ZIPs'):
            updater.release_asset(release, 'Windows', 'AMD64')
        release['assets'] = [{'name': 'Aventurine-3.2.1.zip', 'browser_download_url': 'https://example.invalid/release.zip'}]
        self.assertTrue(updater.release_asset(release, 'Windows', 'AMD64').endswith('/release.zip'))
        with self.assertRaisesRegex(ValueError, 'Windows x64'):
            updater.release_asset(release, 'Darwin', 'arm64')

    def test_swap_keeps_complete_old_installation(self):
        backup = updater.install_release(self.zip, self.target, '3.2.1')
        self.assertEqual((backup / '__init__.py').read_text(), 'old installation')
        self.assertTrue((self.target / 'vendor/ritoshark/ritoshark.pyd').is_file())

    def test_locked_installation_is_not_overwritten(self):
        with patch.object(Path, 'rename', side_effect=PermissionError('DLL locked')):
            with self.assertRaises(PermissionError):
                updater.install_release(self.zip, self.target, '3.2.1')
        self.assertEqual((self.target / '__init__.py').read_text(), 'old installation')
        self.assertEqual(list(self.root.glob('.aventurine-stage-*')), [])

    def test_second_rename_failure_restores_original(self):
        original = Path.rename
        def rename(path, destination):
            if path.parent.name.startswith('.aventurine-stage-'):
                raise OSError('simulated failure')
            return original(path, destination)
        with patch.object(Path, 'rename', rename):
            with self.assertRaises(OSError):
                updater.install_release(self.zip, self.target, '3.2.1')
        self.assertEqual((self.target / '__init__.py').read_text(), 'old installation')
        self.assertEqual(list(self.root.glob('Aventurine_old_*')), [])

    def test_bad_archives_leave_installation_intact(self):
        for extra in ('../outside.py', 'Aventurine/../outside.py', 'Aventurine/C:stream'):
            with self.subTest(extra=extra):
                self.archive(extra)
                with self.assertRaises(ValueError):
                    updater.install_release(self.zip, self.target, '3.2.1')
        self.archive(native=False)
        with self.assertRaisesRegex(ValueError, 'missing'):
            updater.install_release(self.zip, self.target, '3.2.1')
        self.archive()
        with self.assertRaisesRegex(ValueError, 'version'):
            updater.install_release(self.zip, self.target, '3.2.2')
        self.assertEqual((self.target / '__init__.py').read_text(), 'old installation')


if __name__ == '__main__':
    unittest.main()
