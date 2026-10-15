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
from release import read_version, release_plan, source_config
from package_addon import package
from build import snapshot_patch


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
                     'vendor/licenses/LICENSE-MIT', 'io/example.py', 'io/__pycache__/example.pyc',
                     'tests/test_example.py', 'unrelated-project/private.txt', '.git/ignored.txt'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        result = package(self.root, self.root / 'dist')
        with zipfile.ZipFile(result) as archive:
            names = archive.namelist()
            self.assertIn('Aventurine/__init__.py', names)
            self.assertIn('Aventurine/vendor/ritoshark/ritoshark.pyd', names)
            self.assertIn('Aventurine/vendor/licenses/LICENSE-MIT', names)
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


if __name__ == '__main__':
    unittest.main()
