"""Platform checks shared by release packaging and the Blender updater."""
from pathlib import PurePosixPath
import platform
import re


def host_platform(system=None, machine=None):
    system = system if system is not None else platform.system()
    machine = machine if machine is not None else platform.machine()
    if machine.lower() in ('amd64', 'x86_64'):
        if system == 'Windows':
            return 'windows-x64'
        if system == 'Linux':
            return 'linux-x64'
    raise ValueError('Release builds support Windows x64 and Linux x64 only')


def wheel_platform(filename):
    match = re.fullmatch(r'ritoshark-[^-]+-cp3\d+-abi3-([\w.]+)\.whl', filename)
    if match:
        tags = match.group(1).split('.')
        if tags == ['win_amd64']:
            return 'windows-x64'
        if all(re.fullmatch(r'(?:linux|manylinux(?:1|2010|2014|_2_\d+))_x86_64', tag)
               for tag in tags):
            return 'linux-x64'
    raise ValueError('Expected a Windows x64 or glibc Linux x64 RitoShark ABI3 wheel')


def bundle_platform(record):
    target = wheel_platform(record.get('wheel', ''))
    if record.get('platform', target) != target:
        raise ValueError('Bundled platform metadata does not match its Rust wheel')
    return target


def native_module(members, target):
    """Validate exactly one importable ABI3 extension in a vendor directory."""
    allowed = {
        'windows-x64': {'ritoshark/ritoshark.pyd'},
        'linux-x64': {'ritoshark/ritoshark.abi3.so', 'ritoshark/ritoshark.so'},
    }
    if target not in allowed:
        raise ValueError(f'Unsupported release platform: {target}')
    modules = [name for name in members if PurePosixPath(name).parent.as_posix() == 'ritoshark'
               and PurePosixPath(name).name.startswith('ritoshark')
               and name.endswith(('.pyd', '.so'))]
    if not modules:
        raise ValueError('Release is missing the bundled Rust extension')
    if len(modules) != 1 or modules[0] not in allowed[target]:
        raise ValueError(f'Release contains an incompatible or mixed Rust extension for {target}')
    return modules[0]
