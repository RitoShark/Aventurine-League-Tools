import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import zipfile


def vendor(wheel, destination, source_commit=None, source_patch=None):
    wheel = Path(wheel)
    destination = Path(destination)
    if source_commit is not None and not re.fullmatch(r'[0-9a-fA-F]{40}', source_commit):
        raise ValueError('source_commit must be the full 40-character Git commit SHA')
    patch = Path(source_patch).read_bytes() if source_patch else None
    if patch is not None and not source_commit:
        raise ValueError('source_patch requires its base source_commit')
    with zipfile.ZipFile(wheel) as archive:
        members = [name for name in archive.namelist() if name.startswith('ritoshark/')]
        if 'ritoshark/__init__.py' not in members or not any(
            name.endswith(('.pyd', '.so')) for name in members
        ):
            raise ValueError('Expected a RitoShark wheel containing its native extension')
        for name in members:
            path = PurePosixPath(name)
            if '..' in path.parts or path.is_absolute():
                raise ValueError(f'Unsafe wheel member: {name}')
        for name in members:
            target = destination.joinpath(*PurePosixPath(name).parts)
            if name.endswith('/'):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    record = {'wheel': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}
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
