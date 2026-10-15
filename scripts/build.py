"""Build, test, vendor and package Windows x64 with disposable build output.

Run with Python 3.11 + bpy, or pass --blender to run integration checks there.
--library snapshots local edits without changing that repository's index.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

from release import ROOT, source_config
from vendor_ritoshark import vendor
from package_addon import package


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def snapshot_patch(library, destination):
    """Capture tracked and new crate sources using an isolated Git index."""
    env = os.environ.copy()
    env['GIT_INDEX_FILE'] = str(destination.parent / 'source.index')
    run('git', '-C', library, 'read-tree', 'HEAD', env=env)
    run('git', '-C', library, 'add', '--all', '--', 'crates', 'Cargo.toml', 'Cargo.lock',
        'rust-toolchain.toml', env=env)
    data = subprocess.check_output(['git', '-C', str(library), 'diff', '--cached', '--binary', 'HEAD'], env=env)
    destination.write_bytes(data)
    return data


def windows_linker(env, temp, interpreter):
    """Blender's embedded Python lacks python3.lib; derive it from its ABI DLL."""
    roots = list(interpreter.parents)
    dll = next((p / 'python3.dll' for p in roots if (p / 'python3.dll').is_file()), None)
    if dll is None or any((p / 'libs/python3.lib').exists() for p in roots):
        return
    program_files = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))
    vswhere = program_files / 'Microsoft Visual Studio/Installer/vswhere.exe'
    installation = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'], text=True).strip()
    if not installation:
        raise RuntimeError('Install Visual Studio Build Tools with the C++ workload')
    msvc = sorted((Path(installation) / 'VC/Tools/MSVC').iterdir())[-1] / 'bin/HostX64/x64'
    exports = subprocess.check_output([str(msvc / 'dumpbin.exe'), '/exports', str(dll)], text=True)
    symbols = re.findall(r'^\s+\d+\s+[0-9A-F]+\s+([A-Za-z_][A-Za-z_0-9]*)\s+\(forwarded', exports, re.M)
    if not symbols:
        raise RuntimeError('Could not read Blender Python ABI exports')
    definition = temp / 'python3.def'
    definition.write_text('LIBRARY python3.dll\nEXPORTS\n' + '\n'.join(symbols), encoding='ascii')
    run(msvc / 'lib.exe', '/nologo', '/machine:x64', f'/def:{definition}', f'/out:{temp / "python3.lib"}')
    env['LIB'] = str(temp) + os.pathsep + env.get('LIB', '')
    env['PATH'] = str(msvc) + os.pathsep + env['PATH']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, help='Local RitoShark checkout; include its current source edits')
    parser.add_argument('--blender', type=Path, help='Blender executable used for integration and ZIP checks')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('Current release builds target Windows x64')
    commit, patch = source_config(ROOT)
    config = json.loads((ROOT / 'rust-build.json').read_text())
    if args.library:
        args.library = args.library.resolve()
        actual = subprocess.check_output(['git', '-C', str(args.library), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != commit:
            parser.error('Local library HEAD differs from rust-build.json; explicitly update source_commit first')
    if not args.blender:
        # Check before compiling so the user does not discover a missing test runtime afterwards.
        run(sys.executable, '-c', 'import bpy')
    with tempfile.TemporaryDirectory(prefix='aventurine-build-') as temporary:
        temp = Path(temporary)
        source = temp / 'library'
        candidate = temp / 'addon'
        # Keep runtime and tests isolated so a failed validation never replaces the installed bundle.
        candidate.mkdir()
        for name in ('__init__.py', 'LICENSE', 'README.md', 'THIRD_PARTY_LICENSES.md', 'rust-build.json',
                     'io', 'ui', 'utils', 'extras', 'tools', 'icons', 'vendor', 'docs', 'scripts', 'tests'):
            entry = ROOT / name
            if entry.is_dir():
                shutil.copytree(entry, candidate / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            else:
                shutil.copy2(entry, candidate / name)
        if args.library:
            patch = temp / 'local.patch'
            snapshot_patch(args.library, patch)
        run('git', 'clone', '--quiet', '--no-hardlinks',
            args.library or 'https://github.com/RitoShark/RitoShark-Crates.git', source)
        run('git', '-C', source, 'checkout', '--quiet', '--detach', commit)
        if patch and patch.stat().st_size:
            run('git', '-C', source, 'apply', '--check', patch)
            run('git', '-C', source, 'apply', patch)
        else:
            patch = None
        env = os.environ.copy()
        env['RUSTUP_TOOLCHAIN'] = config['rust_toolchain']
        env['CARGO_TARGET_DIR'] = str(temp / 'target')
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        tools = temp / 'tools'
        run(sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--target', tools,
            'maturin==1.15.0', 'pytest>=8,<10')
        # Reuse a matching installed stable compiler instead of installing a duplicate toolchain.
        installed = subprocess.run(['rustc', '+stable', '--version'], capture_output=True, text=True)
        if installed.returncode == 0 and installed.stdout.split()[1] == config['rust_toolchain']:
            env['RUSTUP_TOOLCHAIN'] = 'stable'
        else:
            run('rustup', 'toolchain', 'install', config['rust_toolchain'], '--profile', 'minimal')
        windows_linker(env, temp, Path(sys.executable).resolve())
        maturin = tools / 'bin/maturin.exe'
        run(maturin, 'build', '--release', '--locked', '--manifest-path', source / 'crates/rs_python/Cargo.toml',
            '--interpreter', sys.executable, '--out', temp / 'wheels', env=env)
        run('cargo', 'test', '--release', '--locked', '--manifest-path', source / 'Cargo.toml', '-p', 'rs_mesh', env=env)
        wheel, = (temp / 'wheels').glob('*-abi3-win_amd64.whl')
        metadata = vendor(wheel, candidate / 'vendor', commit, patch)
        for key in ('source_patch', 'source_patch_sha256'):
            config.pop(key, None)
            if key in metadata:
                config[key] = metadata[key]
        (candidate / 'rust-build.json').write_text(json.dumps(config, indent=2) + '\n')
        # Both tests import this exact candidate binary, never a globally installed wheel.
        env['PYTHONPATH'] = os.pathsep.join((str(candidate / 'vendor'), str(tools)))
        dll_parent = next((p for p in Path(sys.executable).resolve().parents if (p / 'python3.dll').exists()), None)
        code = 'import os,pytest; '
        if dll_parent:
            code += f'dll = os.add_dll_directory({str(dll_parent)!r}); '
        code += f'raise SystemExit(pytest.main([{str(source / "crates/rs_python/tests")!r}, "-q", "-p", "no:cacheprovider"]))'
        run(sys.executable, '-c', code, env=env)
        test = candidate / 'tests/test_format_sweep.py'
        if args.blender:
            run(args.blender, '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                '--python-expr', f'import sys,runpy; sys.argv=[{str(test)!r}, "-v"]; runpy.run_path({str(test)!r}, run_name="__main__")', env=env)
        else:
            run(sys.executable, test, '-v', env=env)
        output = temp / 'dist'
        archive = package(candidate, output)
        if args.blender:
            # Avoid treating Blender's sys.executable as a plain Python interpreter.
            import zipfile
            extracted = temp / 'zip-check'
            with zipfile.ZipFile(archive) as z:
                z.extractall(extracted)
            expr = (f'import sys; sys.path.insert(0, {str(candidate / "scripts")!r}); '
                    f'from package_addon import smoke; from pathlib import Path; smoke(Path({str(extracted / "Aventurine")!r}))')
            run(args.blender, '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                '--python-expr', expr, env=env)
        else:
            run(sys.executable, candidate / 'scripts/package_addon.py', '--smoke-dir', output, env=env)
        # Promote only after all checks pass. These files live in a development checkout.
        shutil.copytree(candidate / 'vendor/ritoshark', ROOT / 'vendor/ritoshark', dirs_exist_ok=True)
        for name in ('vendor/wheel.json', 'rust-build.json'):
            shutil.copy2(candidate / name, ROOT / name)
        if patch:
            shutil.copy2(candidate / 'vendor/ritoshark-source.patch', ROOT / 'vendor/ritoshark-source.patch')
        args.output_dir.mkdir(parents=True, exist_ok=True)
        result = args.output_dir / archive.name
        shutil.copy2(archive, result)
        print(f'Validated ZIP: {result}\nSHA256: {hashlib.sha256(result.read_bytes()).hexdigest()}')
    print('Temporary compiler output removed.')


if __name__ == '__main__':
    main()
