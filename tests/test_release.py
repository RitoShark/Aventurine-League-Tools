"""Release automation tests; no Blender or Rust compiler required."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from release import read_version, release_plan, source_config, select_wheel
from package_addon import package
from build import snapshot_patch
from vendor_ritoshark import vendor
from native_platform import host_platform, wheel_platform


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '--quiet')
        self.git('config', 'user.name', 'Release test')
        self.git('config', 'user.email', 'release@example.invalid')
        self.write_version((3, 2, 0))
        self.git('add', '__init__.py')
        self.git('commit', '-qm', 'Initial test version')
        self.before = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], encoding='utf-8')

    def write_version(self, version):
        (self.root / '__init__.py').write_text(f"bl_info = {{'version': {version!r}}}\n", encoding='utf-8')

    def source(self):
        vendor = self.root / 'vendor'
        vendor.mkdir(exist_ok=True)
        (vendor / 'ritoshark-source.patch').write_bytes(b'test patch\n')
        metadata = {'source_commit': 'a' * 40, 'source_patch': 'ritoshark-source.patch',
                    'wheel': 'ritoshark-0.1.0-cp39-abi3-win_amd64.whl',
                    'source_patch_sha256': hashlib.sha256(b'test patch\n').hexdigest()}
        (vendor / 'wheel.json').write_text(json.dumps(metadata))
        (self.root / 'rust-build.json').write_text(json.dumps(metadata))

    def test_only_main_version_increase_publishes(self):
        for version, publish in [((3, 2, 0), 'false'), ((3, 1, 9), 'false'), ((3, 2, 1), 'true')]:
            self.write_version(version)
            plan = release_plan(self.root, 'push', 'refs/heads/main', self.before)
            self.assertEqual(plan['publish'], publish)
            self.assertEqual(plan['build'], publish)
        self.assertEqual(release_plan(self.root, 'push', 'refs/heads/feature', self.before)['publish'], 'false')

    def test_manual_run_builds_without_publishing(self):
        plan = release_plan(self.root, 'workflow_dispatch', 'refs/heads/feature')
        self.assertEqual((plan['build'], plan['publish']), ('true', 'false'))

    def test_manual_publish_recovers_without_version_increase(self):
        plan = release_plan(self.root, 'workflow_dispatch', 'refs/heads/main', manual_publish=True)
        self.assertEqual((plan['build'], plan['publish']), ('true', 'true'))
        with self.assertRaisesRegex(ValueError, 'only from main'):
            release_plan(self.root, 'workflow_dispatch', 'refs/heads/feature', manual_publish=True)
        self.git('tag', '3.2.0')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            release_plan(self.root, 'workflow_dispatch', 'refs/heads/main', manual_publish=True)

    def test_build_snapshot_keeps_local_index_and_includes_new_sources(self):
        (self.root / 'crates').mkdir()
        (self.root / 'crates/new.rs').write_text('new source')
        for name in ('Cargo.toml', 'Cargo.lock', 'rust-toolchain.toml'):
            (self.root / name).write_text('fixture')
        before = self.git('status', '--porcelain')
        data = snapshot_patch(self.root, self.root / 'snapshot.patch')
        self.assertIn(b'crates/new.rs', data)
        (self.root / 'snapshot.patch').unlink()
        (self.root / 'source.index').unlink()
        self.assertEqual(self.git('status', '--porcelain'), before)

    def test_existing_tag_is_never_recreated(self):
        self.write_version((3, 2, 1))
        self.git('tag', '3.2.1')
        self.assertEqual(release_plan(self.root, 'push', 'refs/heads/main', self.before)['build'], 'false')

    def test_source_digest_and_paths_are_checked(self):
        self.source()
        self.assertEqual(source_config(self.root)[0], 'a' * 40)
        (self.root / 'vendor/ritoshark-source.patch').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'digest'):
            source_config(self.root)

    def test_package_includes_runtime_and_excludes_local_projects(self):
        self.source()
        for name in ('LICENSE', 'README.md', 'THIRD_PARTY_LICENSES.md', 'vendor/ritoshark/ritoshark.pyd',
                     'vendor/ritoshark/__init__.py',
                     'vendor/licenses/LICENSE-MIT', 'io/example.py', 'io/__pycache__/example.pyc',
                     'tests/test_example.py', 'unrelated-project/private.txt', '.git/ignored.txt',
                     'docs/guide.md', 'icons/50.png',
                     'docs/images/aventurine-wordmark.png', 'docs/images/aventurine-wordmark.pptx',
                     'docs/images/wordmark-source.txt', 'docs/pose-bind-investigation.md',
                     'docs/yone-export-regression.md'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        result = package(self.root, self.root / 'dist')
        with zipfile.ZipFile(result) as archive:
            names = archive.namelist()
            self.assertIn('Aventurine/__init__.py', names)
            self.assertIn('Aventurine/vendor/ritoshark/ritoshark.pyd', names)
            self.assertIn('Aventurine/vendor/licenses/LICENSE-MIT', names)
            self.assertIn('Aventurine/docs/guide.md', names)
            self.assertIn('Aventurine/icons/50.png', names)
            for excluded in ('docs/images/aventurine-wordmark.png',
                             'docs/images/aventurine-wordmark.pptx',
                             'docs/images/wordmark-source.txt',
                             'docs/pose-bind-investigation.md', 'docs/yone-export-regression.md'):
                self.assertNotIn('Aventurine/' + excluded, names)
            self.assertFalse(any(part in name for name in names for part in ('__pycache__', '/tests/', '.git/', 'private.txt')))

    def test_version_is_read_without_importing_blender(self):
        self.assertEqual(read_version("bl_info = {'version': (3, 2, 1)}\nimport bpy"), (3, 2, 1))
        with self.assertRaises(ValueError):
            read_version("bl_info = {'version': (3, True, 1)}")

    def test_packaging_rejects_an_outdated_extension(self):
        self.source()
        config = json.loads((self.root / 'rust-build.json').read_text())
        config['source_commit'] = 'b' * 40
        (self.root / 'rust-build.json').write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'stale'):
            package(self.root, self.root / 'dist')

    def test_linux_package_is_named_separately_and_includes_shared_libraries(self):
        self.source()
        record = self.root / 'vendor/wheel.json'
        metadata = json.loads(record.read_text())
        metadata['wheel'] = 'ritoshark-0.1.0-cp39-abi3-manylinux_2_28_x86_64.whl'
        metadata['platform'] = 'linux-x64'
        record.write_text(json.dumps(metadata))
        for name in ('LICENSE', 'README.md', 'THIRD_PARTY_LICENSES.md',
                     'vendor/ritoshark/__init__.py', 'vendor/ritoshark/ritoshark.abi3.so',
                     'vendor/ritoshark.libs/libdependency.so'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        archive = package(self.root, self.root / 'dist')
        self.assertEqual(archive.name, 'Aventurine-3.2.0-linux-x64.zip')
        with zipfile.ZipFile(archive) as z:
            self.assertIn('Aventurine/vendor/ritoshark/ritoshark.abi3.so', z.namelist())
            self.assertIn('Aventurine/vendor/ritoshark.libs/libdependency.so', z.namelist())
            self.assertFalse(any(name.endswith('.pyd') for name in z.namelist()))
        (self.root / 'vendor/ritoshark/ritoshark.pyd').write_bytes(b'wrong OS')
        with self.assertRaisesRegex(ValueError, 'mixed'):
            package(self.root, self.root / 'mixed')

    def test_platform_and_abi_checks_reject_unsupported_builds(self):
        self.assertEqual(host_platform('Linux', 'x86_64'), 'linux-x64')
        self.assertEqual(host_platform('Windows', 'AMD64'), 'windows-x64')
        self.assertEqual(wheel_platform('ritoshark-0.1.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'), 'linux-x64')
        for name in ('ritoshark-0.1.0-cp311-cp311-linux_x86_64.whl',
                     'ritoshark-0.1.0-cp39-abi3-manylinux_2_28_aarch64.whl',
                     'ritoshark-0.1.0-cp39-abi3-musllinux_1_2_x86_64.whl',
                     'ritoshark-0.1.0-cp39-abi3-macosx_11_0_arm64.whl'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                wheel_platform(name)

    def test_wheel_selection_is_platform_specific_and_unambiguous(self):
        windows = self.root / 'ritoshark-0.1.0-cp39-abi3-win_amd64.whl'
        linux = self.root / 'ritoshark-0.1.0-cp39-abi3-manylinux_2_28_x86_64.whl'
        windows.touch(); linux.touch()
        self.assertEqual(select_wheel(self.root, 'windows-x64'), windows)
        self.assertEqual(select_wheel(self.root, 'linux-x64'), linux)
        (self.root / 'ritoshark-0.1.1-cp39-abi3-manylinux_2_28_x86_64.whl').touch()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            select_wheel(self.root, 'linux-x64')

    def test_vendoring_replaces_old_platform_and_keeps_linux_dependencies(self):
        destination = self.root / 'vendor'
        (destination / 'licenses').mkdir(parents=True)
        (destination / 'licenses/LICENSE').write_text('keep')
        for target, suffix, module in [('windows-x64', 'win_amd64', 'ritoshark.pyd'),
                                       ('linux-x64', 'manylinux_2_28_x86_64', 'ritoshark.abi3.so'),
                                       ('windows-x64', 'win_amd64', 'ritoshark.pyd')]:
            wheel = self.root / f'ritoshark-0.1.0-cp39-abi3-{suffix}.whl'
            with zipfile.ZipFile(wheel, 'w') as z:
                z.writestr('ritoshark/__init__.py', 'from .ritoshark import *')
                z.writestr('ritoshark/' + module, b'test module')
                if target == 'linux-x64':
                    z.writestr('ritoshark.libs/libdependency.so', b'library')
            metadata = vendor(wheel, destination, 'a' * 40)
            self.assertEqual(metadata['platform'], target)
            self.assertEqual(sorted(p.name for p in (destination / 'ritoshark').iterdir()), ['__init__.py', module])
            self.assertEqual((destination / 'ritoshark.libs/libdependency.so').exists(), target == 'linux-x64')
            self.assertEqual((destination / 'licenses/LICENSE').read_text(), 'keep')
        before = (destination / 'wheel.json').read_bytes()
        with zipfile.ZipFile(wheel, 'w') as z:
            z.writestr('ritoshark/__init__.py', '')
            z.writestr('ritoshark/ritoshark.abi3.so', b'wrong OS')
        with self.assertRaises(ValueError):
            vendor(wheel, destination, 'a' * 40)
        self.assertEqual((destination / 'wheel.json').read_bytes(), before)
        self.assertTrue((destination / 'ritoshark/ritoshark.pyd').exists())


if __name__ == '__main__':
    unittest.main()
