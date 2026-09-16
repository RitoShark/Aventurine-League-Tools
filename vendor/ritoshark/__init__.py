from .ritoshark import *
from . import ritoshark as _native

__doc__ = _native.__doc__
__version__ = _native.__version__

__all__ = [
    "AnimFrame",
    "AnimTrack",
    "Anm",
    "FormatError",
    "Joint",
    "MapGeo",
    "MapModel",
    "MapSubmesh",
    "ParseError",
    "Scb",
    "ScbFace",
    "Sco",
    "Skl",
    "Skn",
    "Submesh",
    "Tex",
    "UnsupportedVersion",
    "Wad",
    "WadChunk",
    "WriteError",
    "read_bin",
    "read_bin_bytes",
    "wad_hash",
    "__version__",
]

del _native
del ritoshark
