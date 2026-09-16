import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def vendor(wheel, destination):
    wheel = Path(wheel)
    destination = Path(destination)
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
    (destination / 'wheel.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('wheel', type=Path)
    parser.add_argument('--destination', type=Path, default=Path(__file__).resolve().parents[1] / 'vendor')
    args = parser.parse_args()
    print(json.dumps(vendor(args.wheel, args.destination), indent=2))
