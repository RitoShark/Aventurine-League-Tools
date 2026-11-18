"""Updater tests use disposable installations; no downloads or live add-on changes."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch
import zipfile

# Load the updater's relative utility imports without importing bpy/__init__.py.
root = Path(__file__).resolve().parents[1]
namespace = types.ModuleType('_aventurine_updater_tests')
namespace.__path__ = [str(root)]
sys.modules[namespace.__name__] = namespace
spec = importlib.util.spec_from_file_location(namespace.__name__ + '.tools.update_install', root / 'tools/update_install.py')
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

    def archive(self, extra=None, native=True, target=None, module=None, wheel=None):
        target = target or updater.host_platform()
        self.module = module or ('ritoshark.pyd' if target == 'windows-x64' else 'ritoshark.abi3.so')
        wheel = wheel or ('ritoshark-0.1.0-cp39-abi3-win_amd64.whl' if target == 'windows-x64'
                          else 'ritoshark-0.1.0-cp39-abi3-manylinux_2_28_x86_64.whl')
        with zipfile.ZipFile(self.zip, 'w') as z:
            z.writestr('Aventurine/__init__.py', "bl_info = {'version': (3, 2, 1)}\n")
            z.writestr('Aventurine/vendor/wheel.json', json.dumps({'wheel': wheel}))
            z.writestr('Aventurine/vendor/ritoshark/__init__.py', 'from .ritoshark import *\n')
            if native:
                z.writestr('Aventurine/vendor/ritoshark/' + self.module, b'new native module')
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
        self.assertTrue((self.target / 'vendor/ritoshark' / self.module).is_file())

    def test_linux_selects_its_asset_and_never_falls_back_to_windows(self):
        windows = {'name': 'Aventurine-3.2.1.zip', 'browser_download_url': 'https://example.invalid/windows.zip'}
        linux = {'name': 'Aventurine-3.2.1-linux-x64.zip', 'browser_download_url': 'https://example.invalid/linux.zip'}
        release = {'tag_name': 'v3.2.1', 'assets': [windows, linux]}
        self.assertTrue(updater.release_asset(release, 'Linux', 'x86_64').endswith('/linux.zip'))
        self.assertTrue(updater.release_asset(release, 'Windows', 'AMD64').endswith('/windows.zip'))
        release['assets'] = [windows]
        with self.assertRaisesRegex(ValueError, 'no packaged linux-x64'):
            updater.release_asset(release, 'Linux', 'x86_64')
        with self.assertRaises(ValueError):
            updater.release_asset(release, 'Linux', 'aarch64')

    def test_both_platform_archives_validate_and_reject_the_wrong_os(self):
        for target, system in [('windows-x64', 'Windows'), ('linux-x64', 'Linux')]:
            with self.subTest(target=target):
                self.archive(target=target)
                destination = self.root / target
                result = updater.unpack_release(self.zip, destination, '3.2.1', system, 'x86_64')
                self.assertTrue((result / 'vendor/ritoshark' / self.module).is_file())
                wrong = self.root / (target + '-wrong')
                with self.assertRaisesRegex(ValueError, 'incompatible Rust wheel'):
                    updater.unpack_release(self.zip, wrong, '3.2.1', 'Linux' if system == 'Windows' else 'Windows', 'x86_64')
                self.assertFalse(wrong.exists())

    def test_wrong_or_mixed_native_modules_are_rejected_before_extracting(self):
        for module, extra in [('ritoshark.pyd', None),
                              ('ritoshark.abi3.so', 'Aventurine/vendor/ritoshark/ritoshark.pyd'),
                              ('ritoshark.cpython-311-x86_64-linux-gnu.so', None)]:
            with self.subTest(module=module, extra=extra):
                self.archive(target='linux-x64', module=module, extra=extra)
                dest = self.root / 'bad-native'
                with self.assertRaisesRegex(ValueError, 'incompatible or mixed'):
                    updater.unpack_release(self.zip, dest, '3.2.1', 'Linux', 'x86_64')
                self.assertFalse(dest.exists())

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
