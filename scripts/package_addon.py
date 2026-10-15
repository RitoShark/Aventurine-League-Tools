"""Package the add-on using the release allowlist, without build tools or caches."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from release import ROOT, read_version, source_config


def package(root, output_dir):
    source_config(root)
    desired = json.loads((root / 'rust-build.json').read_text(encoding='utf-8'))
    bundled = json.loads((root / 'vendor/wheel.json').read_text(encoding='utf-8'))
    if any(desired.get(key) != bundled.get(key) for key in ('source_commit', 'source_patch', 'source_patch_sha256')):
        raise ValueError('The bundled extension is stale; rebuild and vendor the configured Rust source')
    version = '.'.join(map(str, read_version((root / '__init__.py').read_text(encoding='utf-8'))))
    if not (root / 'vendor/ritoshark/ritoshark.pyd').is_file():
        raise ValueError('Build and vendor the Windows extension before packaging')
    files = [root / name for name in ('__init__.py', 'LICENSE', 'README.md', 'THIRD_PARTY_LICENSES.md')]
    for directory in ('io', 'ui', 'utils', 'extras', 'tools', 'icons', 'vendor', 'docs'):
        files.extend(p for p in (root / directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'))
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f'Aventurine-{version}.zip'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in sorted(files):
            archive.write(file, 'Aventurine/' + file.relative_to(root).as_posix())
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Corrupt release archive')
    print(json.dumps({'zip': str(path), 'bytes': path.stat().st_size,
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
    return path


def smoke(root):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('Aventurine', root / '__init__.py')
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    from Aventurine.utils.backend import require_ritoshark
    rs = require_ritoshark()
    assert hasattr(rs.Skn, 'colors') and hasattr(rs.Skn, 'tangents') and hasattr(rs.Skl, 'flags') and hasattr(rs.Scb, 'trailing') and hasattr(rs.Scb, 'colors')
    addon.register()
    addon.unregister()
    print('Packaged add-on registers and loads the Rust extension')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--output-dir', type=Path)
    group.add_argument('--smoke-dir', type=Path)
    group.add_argument('--smoke-extracted', type=Path)
    args = parser.parse_args()
    if args.output_dir:
        package(ROOT, args.output_dir)
    elif args.smoke_extracted:
        smoke(args.smoke_extracted)
    else:
        archives = list(args.smoke_dir.glob('Aventurine-*.zip'))
        if len(archives) != 1:
            raise ValueError('Expected one release ZIP')
        # Test in a subprocess so Windows releases the DLL before cleanup.
        with tempfile.TemporaryDirectory(prefix='aventurine-zip-check-') as temp:
            with zipfile.ZipFile(archives[0]) as archive:
                archive.extractall(temp)
            subprocess.run([sys.executable, __file__, '--smoke-extracted',
                            str(Path(temp) / 'Aventurine')], check=True)


if __name__ == '__main__':
    main()
