"""Validate release artifacts and replace an installation without overwriting loaded DLLs."""
import ast
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import uuid
import zipfile

from ..utils.release_platform import host_platform, bundle_platform, native_module


def release_asset(release, system=None, machine=None):
    target = host_platform(system, machine)
    version = release.get('tag_name', '').removeprefix('v')
    accepted = [f'aventurine-{version}-{target}.zip']
    if target == 'windows-x64':
        accepted.append(f'aventurine-{version}.zip')  # Existing Windows releases/updaters.
    for name in accepted:
        for asset in release.get('assets', []):
            if asset.get('name', '').lower() == name:
                url = asset.get('browser_download_url', '')
                if url.startswith('https://'):
                    return url
    raise ValueError(f'This release has no packaged {target} add-on; source ZIPs cannot be installed')


def unpack_release(archive_path, destination, expected_version, system=None, machine=None):
    destination = Path(destination)
    target = host_platform(system, machine)
    with zipfile.ZipFile(archive_path) as archive:
        names = set()
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename
                    or ':' in item.filename or not path.parts or path.parts[0] != 'Aventurine'
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Invalid path in update archive')
            folded = item.filename.casefold()
            if folded in names:
                raise ValueError('Duplicate path in update archive')
            names.add(folded)
        required = {'Aventurine/__init__.py', 'Aventurine/vendor/wheel.json',
                    'Aventurine/vendor/ritoshark/__init__.py'}
        if not required.issubset(set(archive.namelist())):
            raise ValueError('Update is missing the bundled Rust extension or metadata')
        record = json.loads(archive.read('Aventurine/vendor/wheel.json'))
        if bundle_platform(record) != target:
            raise ValueError('Update contains an incompatible Rust wheel')
        prefix = 'Aventurine/vendor/'
        native_module([name[len(prefix):] for name in archive.namelist() if name.startswith(prefix)], target)
        version = None
        for node in ast.parse(archive.read('Aventurine/__init__.py')).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in node.targets):
                version = '.'.join(map(str, ast.literal_eval(node.value)['version']))
                break
        if version != expected_version.removeprefix('v'):
            raise ValueError('Update version does not match the selected release')
        archive.extractall(destination)
    return destination / 'Aventurine'


def install_release(archive_path, target, version):
    """Stage on the destination volume. Keep the old DLL alive until Blender exits.

    A failed rename never falls back to copying over the running add-on. If the
    second rename fails, restore the complete original installation.
    """
    target = Path(target).resolve()
    if not target.is_dir() or not (target / '__init__.py').is_file():
        raise ValueError('Cannot identify the existing add-on installation')
    with tempfile.TemporaryDirectory(prefix='.aventurine-stage-', dir=target.parent) as temp:
        source = unpack_release(archive_path, temp, version)
        backup = target.with_name(f'{target.name}_old_{uuid.uuid4().hex}')
        target.rename(backup)
        try:
            source.rename(target)
        except Exception:
            backup.rename(target)
            raise
        # A loaded .pyd may lock the backup. Startup cleanup handles it after exit.
        return backup
