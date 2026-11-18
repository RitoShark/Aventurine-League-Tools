import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath
import zipfile

from native_platform import wheel_platform, native_module


def vendor(wheel, destination, source_commit=None, source_patch=None):
    wheel = Path(wheel)
    destination = Path(destination)
    target_platform = wheel_platform(wheel.name)
    if source_commit is not None and not re.fullmatch(r'[0-9a-fA-F]{40}', source_commit):
        raise ValueError('source_commit must be the full 40-character Git commit SHA')
    patch = Path(source_patch).read_bytes() if source_patch else None
    if patch is not None and not source_commit:
        raise ValueError('source_patch requires its base source_commit')
    with zipfile.ZipFile(wheel) as archive:
        members = [name for name in archive.namelist()
                   if name.startswith(('ritoshark/', 'ritoshark.libs/'))]
        if 'ritoshark/__init__.py' not in members:
            raise ValueError('Expected a RitoShark wheel containing its native extension')
        native_module(members, target_platform)
        payload = {}
        for name in members:
            path = PurePosixPath(name)
            if '..' in path.parts or path.is_absolute() or '\\' in name or ':' in name:
                raise ValueError(f'Unsafe wheel member: {name}')
            if not name.endswith('/'):
                payload[name] = archive.read(name)
    # Validate/read the whole wheel first. Replace only the package and its
    # bundled shared libraries, so switching OS cannot leave stale extensions.
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('ritoshark', 'ritoshark.libs'):
        target = destination / name
        if target.resolve().parent != destination.resolve() or target.is_symlink():
            raise ValueError(f'Unsafe vendor destination: {target}')
    for name in ('ritoshark', 'ritoshark.libs'):
        target = destination / name
        if target.exists():
            shutil.rmtree(target)
    for name, data in payload.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    record = {'wheel': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
              'platform': target_platform}
    if source_commit:
        record['source_commit'] = source_commit.lower()
    if patch is not None:
        patch_path = destination / 'ritoshark-source.patch'
        patch_path.write_bytes(patch)
        record['source_patch'] = patch_path.name
        record['source_patch_sha256'] = hashlib.sha256(patch).hexdigest()
    (destination / 'wheel.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('wheel', type=Path)
    parser.add_argument('--destination', type=Path, default=Path(__file__).resolve().parents[1] / 'vendor')
    parser.add_argument('--source-commit', help='Full Git SHA of the library checkout, or base commit when supplying a patch')
    parser.add_argument('--source-patch', type=Path, help='Patch applied to the base library commit before building')
    args = parser.parse_args()
    print(json.dumps(vendor(args.wheel, args.destination, args.source_commit, args.source_patch), indent=2))
