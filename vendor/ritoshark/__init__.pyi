__version__: str

class FormatError(Exception): ...
class ParseError(FormatError): ...
class UnsupportedVersion(FormatError): ...
class WriteError(FormatError): ...

class Submesh:
    name: str
    vertex_start: int
    vertex_count: int
    index_start: int
    index_count: int
    def __init__(
        self,
        name: str,
        vertex_start: int,
        vertex_count: int,
        index_start: int,
        index_count: int,
    ) -> None: ...

class Skn:
    @staticmethod
    def from_path(path: str) -> Skn: ...
    @staticmethod
    def from_bytes(data: bytes) -> Skn: ...
    @staticmethod
    def new(
        *,
        positions: bytes,
        normals: bytes,
        uvs: bytes,
        blend_indices: bytes,
        blend_weights: bytes,
        indices: bytes,
        submeshes: list[Submesh],
    ) -> Skn:
        """Builds a new .skn from packed buffers. `indices` values above 65535 or
        `blend_indices` values above 255 raise WriteError."""
    @property
    def version(self) -> tuple[int, int]: ...
    @property
    def vertex_count(self) -> int: ...
    @property
    def positions(self) -> bytes:
        """Tightly packed little-endian, 3 x float32 per vertex."""
    @property
    def normals(self) -> bytes:
        """Tightly packed little-endian, 3 x float32 per vertex."""
    @property
    def uvs(self) -> bytes:
        """Tightly packed little-endian, 2 x float32 per vertex."""
    @property
    def blend_indices(self) -> bytes:
        """Tightly packed little-endian, 4 x uint32 per vertex."""
    @property
    def blend_weights(self) -> bytes:
        """Tightly packed little-endian, 4 x float32 per vertex."""
    @property
    def indices(self) -> bytes:
        """Tightly packed little-endian, 1 x uint32 per corner. Narrowed to uint16 on
        write; values above 65535 raise WriteError."""
    @property
    def submeshes(self) -> list[Submesh]: ...
    def to_bytes(self) -> bytes: ...
    def to_path(self, path: str) -> None: ...

class ScbFace:
    material: str
    indices: tuple[int, int, int]
    uvs: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    def __init__(
        self,
        material: str,
        indices: tuple[int, int, int],
        uvs: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    ) -> None: ...

class Scb:
    @staticmethod
    def from_path(path: str) -> Scb: ...
    @staticmethod
    def from_bytes(data: bytes) -> Scb: ...
    @staticmethod
    def new(name: str, positions: bytes, faces: list[ScbFace]) -> Scb: ...
    def replace_geometry(self, positions: bytes, faces: list[ScbFace]) -> None:
        """Swaps in new geometry while preserving what a caller cannot reconstruct: the
        container version, the raw flag word, the vertexType word, per-vertex colors, and the
        opaque trailing block holding per-face VCP data and the local-origin/pivot vectors.
        Use this to edit a file read from disk; use new() for a mesh built from scratch.
        Raises WriteError if an index is out of range, or if the mesh carries per-vertex
        colors and the replacement changes the vertex count."""
    name: str
    @property
    def central(self) -> tuple[float, float, float]: ...
    @property
    def positions(self) -> bytes:
        """Tightly packed little-endian, 3 x float32 per vertex."""
    @property
    def faces(self) -> list[ScbFace]: ...
    def to_bytes(self) -> bytes: ...
    def to_path(self, path: str) -> None: ...

class Sco:
    """Read-only: the game dropped the text .sco format, so rs_mesh writes only the
    binary .scb form. There is no to_bytes/to_path."""

    @staticmethod
    def from_path(path: str) -> Sco: ...
    @staticmethod
    def from_bytes(data: bytes) -> Sco: ...
    @property
    def name(self) -> str: ...
    @property
    def central(self) -> tuple[float, float, float]: ...
    @property
    def positions(self) -> bytes:
        """Tightly packed little-endian, 3 x float32 per vertex."""
    @property
    def faces(self) -> list[ScbFace]: ...

class Joint:
    name: str
    id: int
    parent_id: int
    radius: float
    hash: int
    flags: int
    local_translation: tuple[float, float, float]
    local_scale: tuple[float, float, float]
    local_rotation: tuple[float, float, float, float]
    inverse_bind_translation: tuple[float, float, float]
    inverse_bind_scale: tuple[float, float, float]
    inverse_bind_rotation: tuple[float, float, float, float]
    def __init__(
        self,
        name: str,
        id: int,
        parent_id: int,
        radius: float,
        local_translation: tuple[float, float, float],
        local_scale: tuple[float, float, float],
        local_rotation: tuple[float, float, float, float],
        inverse_bind_translation: tuple[float, float, float],
        inverse_bind_scale: tuple[float, float, float],
        inverse_bind_rotation: tuple[float, float, float, float],
        flags: int = 0,
    ) -> None: ...

class Skl:
    @staticmethod
    def from_path(path: str) -> Skl: ...
    @staticmethod
    def from_bytes(data: bytes) -> Skl: ...
    @staticmethod
    def new(
        joints: list[Joint],
        influences: list[int],
        name: str = "",
        asset: str = "",
    ) -> Skl: ...
    @property
    def name(self) -> str: ...
    @property
    def asset(self) -> str: ...
    @property
    def joints(self) -> list[Joint]: ...
    @property
    def influences(self) -> list[int]: ...
    def to_bytes(self) -> bytes: ...
    def to_path(self, path: str) -> None: ...

class AnimFrame:
    time: float
    rotation: tuple[float, float, float, float]
    translation: tuple[float, float, float]
    scale: tuple[float, float, float]
    def __init__(
        self,
        time: float,
        rotation: tuple[float, float, float, float],
        translation: tuple[float, float, float],
        scale: tuple[float, float, float],
    ) -> None: ...

class AnimTrack:
    joint_hash: int
    frames: list[AnimFrame]
    def __init__(self, joint_hash: int, frames: list[AnimFrame]) -> None: ...

class Anm:
    @staticmethod
    def from_path(path: str) -> Anm: ...
    @staticmethod
    def from_bytes(data: bytes) -> Anm: ...
    @staticmethod
    def new(fps: float, tracks: list[AnimTrack]) -> Anm: ...
    @property
    def fps(self) -> float: ...
    @property
    def frame_count(self) -> int: ...
    @property
    def is_byte_exact(self) -> bool:
        """True when the original source bytes are still reproduced exactly on write.
        Becomes False once make_editable() is called."""
    def make_editable(self) -> None:
        """Switches from byte-exact passthrough to an editable track representation.
        After this call, to_bytes()/to_path() emit tracks rather than the original bytes."""
    @property
    def tracks(self) -> list[AnimTrack]: ...
    def to_bytes(self) -> bytes: ...
    def to_path(self, path: str) -> None: ...

class Tex:
    """Textures have no writer: .tex encoding is out of scope for the Python bindings."""

    @staticmethod
    def from_path(path: str) -> Tex: ...
    @staticmethod
    def from_bytes(data: bytes) -> Tex: ...
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...
    @property
    def format(self) -> str: ...
    @property
    def mip_count(self) -> int: ...
    @property
    def rgba(self) -> bytes:
        """Decodes on every access. 4 x uint8 per pixel, row-major, top-down.
        Empty if width or height is 0."""
    @property
    def rgba_f32(self) -> bytes:
        """Decodes on every access. 4 x float32 per pixel in 0..1, row-flipped
        bottom-up to match Blender's image.pixels layout. Empty if width or
        height is 0."""

class WadChunk:
    path_hash: int
    compressed_size: int
    uncompressed_size: int
    compression: str
    is_duplicated: bool

class Wad:
    @staticmethod
    def from_path(path: str) -> Wad: ...
    @staticmethod
    def from_bytes(data: bytes) -> Wad: ...
    @property
    def version(self) -> tuple[int, int]: ...
    @property
    def chunks(self) -> list[WadChunk]: ...
    def read(self, path_hash: int) -> bytes | None:
        """Decompresses and returns the chunk with the given path hash, or None if no
        such chunk exists."""
    def read_path(self, path: str) -> bytes | None:
        """Equivalent to read(wad_hash(path))."""
    def __len__(self) -> int: ...

def wad_hash(path: str) -> int:
    """xxh64 of the lowercased path, seed 0 — the WAD chunk path hash. Always lowercases
    internally; never lowercase the input yourself before calling this."""

def build_wad(
    chunks: dict[str, bytes | bytearray | memoryview] | dict[int, bytes | bytearray | memoryview],
    zstd_level: int = ...,
) -> bytes:
    """Builds a WAD v3.4 archive from a mapping of chunk path (str, hashed with wad_hash)
    or path hash (int) to uncompressed chunk bytes, and returns the archive bytes. The
    whole mapping is taken eagerly rather than as a callback, since the underlying builder
    pulls each chunk's data twice (once to size it, once to write it) and a Python callback
    reading from a file or generator could silently return different bytes on the second
    pass. zstd_level defaults to the library's DEFAULT_ZSTD_LEVEL. If a str key and an int
    key hash to the same path, the one later in dict order wins. Raises TypeError for a
    non-str/int key, a bool key, or a value that is not bytes/bytearray/memoryview;
    WriteError if the archive cannot be built."""

def build_wad_to_path(
    path: str,
    chunks: dict[str, bytes | bytearray | memoryview] | dict[int, bytes | bytearray | memoryview],
    zstd_level: int = ...,
) -> None:
    """Equivalent to build_wad but writes the archive directly to path."""

def read_bin(path: str) -> dict:
    """Reads a .bin file into a plain Python dict. This view is lossy by design: it drops
    the hash-versus-resolved-name duality, the LIST/LIST2 distinction, and duplicate map
    keys. There is no corresponding write function."""

def read_bin_bytes(data: bytes) -> dict:
    """Same as read_bin but from an in-memory buffer."""

def read_bin_editable(path: str) -> dict:
    """Reads a .bin file into a faithful, editable document tree: entries is a list of
    {"hash", "class", "fields"}, fields is a dict keyed by raw field hash, and containers
    (list/list2/pointer/embed/map/option) are tagged dicts carrying their declared element
    type. write_bin_bytes(read_bin_editable(f)) is byte-identical to f for any valid .bin.
    Raises FormatError on malformed input or nesting past the recursion guard."""

def read_bin_editable_bytes(data: bytes) -> dict:
    """Same as read_bin_editable but from an in-memory buffer."""

def write_bin(path: str, doc: dict) -> None:
    """Writes an editable document tree (as produced by read_bin_editable, or built by
    hand) to a .bin file at path. Validates the whole document before writing any bytes;
    raises WriteError carrying the path to the offending value on malformed structure, an
    unknown __type__, a wrong-typed value, or nesting past the recursion guard."""

def write_bin_bytes(doc: dict) -> bytes:
    """Same as write_bin but returns the encoded bytes instead of writing to a path."""

def bin_to_text(path: str, hashes: str | None = None) -> str:
    """Renders a .bin file as #PROP_text (the ritobin text form). With hashes, a path to a
    hash-dictionary file, field/class/entry names resolve to their original strings instead
    of raw hashes. Every FNV1a-keyed name is re-hashed and checked against its own dictionary
    entry before being emitted; a stale, wrong, or colliding entry silently falls back to the
    raw hash instead of being trusted, so a bad dictionary can never corrupt the round-trip.
    XXH64-keyed file names cannot be verified this way and are always resolved as given.
    Raises FormatError if path or hashes cannot be read or parsed."""

def bin_to_text_bytes(data: bytes, hashes: str | None = None) -> str:
    """Same as bin_to_text but from an in-memory .bin buffer."""

def text_to_bin_path(path: str, text: str) -> None:
    """Parses #PROP_text and writes the resulting .bin file to path. Raises FormatError on
    malformed text."""

def text_to_bin_bytes(text: str) -> bytes:
    """Parses #PROP_text and returns the encoded .bin bytes. Raises FormatError on malformed
    text."""

class MapSubmesh:
    hash: int
    name: str
    index_start: int
    index_count: int
    min_vertex: int
    max_vertex: int

class MapModel:
    """Does not own its vertex/index data: positions(), normals(), uvs() and indices()
    de-interleave the referenced shared buffer on every call, so they are methods, not
    properties. A model is a read-only view; edit it through the containing MapGeo, whose
    replace_geometry/set_transform/set_layer take the model's index."""

    @property
    def name(self) -> str: ...
    @property
    def vertex_count(self) -> int: ...
    @property
    def layer(self) -> int: ...
    @property
    def transform(self) -> list[float]:
        """16 floats, column-major 4x4 matrix."""
    @property
    def bounds(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]: ...
    @property
    def disable_backface_culling(self) -> bool: ...
    @property
    def texture_overrides(self) -> list[tuple[int, str]]: ...
    @property
    def submeshes(self) -> list[MapSubmesh]: ...
    def positions(self) -> bytes:
        """METHOD, not a property. Tightly packed little-endian, 3 x float32 per vertex."""
    def normals(self) -> bytes:
        """METHOD, not a property. 3 x float32 per vertex, or empty when this model's layout
        carries no normals. Packed formats decode to the normalised 0..=1 range they store."""
    def uvs(self) -> bytes:
        """METHOD, not a property. 2 x float32 per vertex for channel 0, or empty when this
        model's layout carries none."""
    def indices(self) -> bytes:
        """METHOD, not a property. Tightly packed little-endian, 1 x uint32 per corner."""

class MapGeo:
    """A file that was read can be edited. Construction from scratch stays out of scope:
    the scene graphs, bucketed geometry, planar reflectors and per-version baked lighting
    the format carries are not derivable from a mesh, so an edit keeps them from the file
    it started with."""

    @staticmethod
    def from_path(path: str) -> MapGeo: ...
    @staticmethod
    def from_bytes(data: bytes) -> MapGeo: ...
    @property
    def version(self) -> int: ...
    @property
    def models(self) -> list[MapModel]: ...
    def replace_geometry(
        self,
        index: int,
        name: str,
        positions: bytes,
        indices: bytes,
        normals: bytes = ...,
        uvs: bytes = ...,
        submeshes: list[tuple[str, int, int]] = ...,
    ) -> None:
        """Replaces one model's geometry, keeping its transform, layer, lighting, texture
        overrides and scene-graph association. positions/normals are 3 x float32 per vertex,
        uvs 2 x float32, indices 1 x uint16 per corner, tightly packed little-endian.
        submeshes is (name, index_start, index_count); empty draws everything as one."""

    def add_model(
        self,
        name: str,
        positions: bytes,
        indices: bytes,
        normals: bytes = ...,
        uvs: bytes = ...,
        transform: list[float] | None = ...,
        layer: int = 255,
        submeshes: list[tuple[str, int, int]] = ...,
    ) -> int:
        """Appends a model and returns its index. transform is 16 floats, column-major."""

    def remove_model(self, index: int) -> None: ...
    def set_transform(self, index: int, transform: list[float]) -> None: ...
    def set_layer(self, index: int, layer: int) -> None: ...
    def to_bytes(self) -> bytes: ...
    def to_path(self, path: str) -> None: ...
    def __len__(self) -> int: ...
