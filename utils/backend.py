from importlib import import_module


def require_ritoshark():
    try:
        return import_module('..vendor.ritoshark', __package__)
    except ImportError as error:
        raise RuntimeError(
            'Aventurine could not load its RitoShark Python binding. Install the '
            'release ZIP for your operating system and restart Blender. Source '
            'checkouts must first run scripts/vendor_ritoshark.py with a matching wheel.'
        ) from error
