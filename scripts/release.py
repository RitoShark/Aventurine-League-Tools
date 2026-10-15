"""Release planning and reproducible selection of the Rust binding source."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def read_version(source):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in node.targets):
            version = ast.literal_eval(node.value)['version']
            if (isinstance(version, tuple) and len(version) == 3
                    and all(type(v) is int and v >= 0 for v in version)):
                return version
    raise ValueError('bl_info must contain a three-number version tuple')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], encoding='utf-8')


def release_plan(root, event_name, ref, before=None, manual_publish=False):
    current = read_version((root / '__init__.py').read_text(encoding='utf-8'))
    version = '.'.join(map(str, current))
    manual = event_name == 'workflow_dispatch'
    publish = False
    if event_name == 'push' and ref == 'refs/heads/main':
        previous = None
        if before and before != '0' * 40:
            if not re.fullmatch(r'[0-9a-fA-F]{40}', before):
                raise ValueError('Invalid previous commit SHA')
            previous = read_version(git(root, 'show', f'{before}:__init__.py'))
        publish = (previous is None or current > previous) and not git(root, 'tag', '--list', version).strip()
    if manual and manual_publish:
        if ref != 'refs/heads/main':
            raise ValueError('Manual publishing is allowed only from main')
        if git(root, 'tag', '--list', version).strip():
            raise ValueError(f'Release tag {version} already exists; rerun the original failed publish job if needed')
        publish = True
    return {'version': version, 'build': str(manual or publish).lower(), 'publish': str(publish).lower()}


def source_config(root):
    record = json.loads((root / 'rust-build.json').read_text(encoding='utf-8'))
    commit = record.get('source_commit', '')
    if not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
        raise ValueError('rust-build.json must pin the full library source_commit')
    patch = None
    if record.get('source_patch'):
        patch = (root / 'vendor' / record['source_patch']).resolve()
        if patch.parent != (root / 'vendor').resolve():
            raise ValueError('The source patch must be directly inside vendor/')
        if hashlib.sha256(patch.read_bytes()).hexdigest() != record.get('source_patch_sha256'):
            raise ValueError('Library source patch digest does not match rust-build.json')
    return commit.lower(), patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'patch', 'vendor'])
    parser.add_argument('--library', type=Path)
    parser.add_argument('--wheels', type=Path)
    args = parser.parse_args()
    commit, patch = source_config(ROOT)
    if args.command == 'plan':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        values = release_plan(ROOT, os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REF'], event.get('before'), event.get('inputs', {}).get('publish') in (True, 'true'))
        values['library_commit'] = commit
        values['rust_toolchain'] = json.loads((ROOT / 'rust-build.json').read_text())['rust_toolchain']
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            for key, value in values.items():
                output.write(f'{key}={value}\n')
        print(json.dumps(values, indent=2))
    elif args.command == 'patch':
        if not args.library or git(args.library, 'rev-parse', 'HEAD').strip() != commit:
            raise ValueError('Library checkout does not match the pinned source commit')
        if git(args.library, 'status', '--porcelain').strip():
            raise ValueError('Apply the source patch to a clean checkout')
        if patch:
            git(args.library, 'apply', '--check', str(patch))
            git(args.library, 'apply', str(patch))
        print(f'Prepared Rust source at {commit}')
    else:
        from vendor_ritoshark import vendor
        wheels = list(args.wheels.glob('*-abi3-win_amd64.whl')) if args.wheels else []
        if len(wheels) != 1:
            raise ValueError('Expected exactly one freshly built Windows x64 ABI3 wheel')
        print(json.dumps(vendor(wheels[0], ROOT / 'vendor', commit, patch), indent=2))


if __name__ == '__main__':
    main()
