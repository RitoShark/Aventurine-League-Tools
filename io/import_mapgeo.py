# MAPGEO importer — League of Legends map geometry (.mapgeo, versions 5-18).
#
# The binary format follows league-toolkit's ltk_mapgeo crate (ground truth) and
# LtMAO's pyRitoFile/mapgeo.py (algorithm reference, GPL-3 like this addon).
# Parsing is bulk numpy — vertex/index buffers are decoded with np.frombuffer on
# structured dtypes, never per-vertex Python objects.
#
# This module is split in two layers:
#   - read_mapgeo(): pure Python + numpy, no bpy import. Testable outside Blender.
#   - build_scene(): Blender scene construction (objects, UVs, colors, layers).
#
# Bucket grids / planar reflectors trail the mesh records and are never decoded;
# we record the byte offset where they start so a future exporter can copy
# everything past it straight from the source file (same approach as lol_maya).

import struct
import numpy as np

MAPGEO_MAGIC = b'OEGM'
SUPPORTED_VERSIONS = (5, 6, 7, 9, 11, 12, 13, 14, 15, 16, 17, 18)

# Vertex element semantic ids (file order is authoritative; do not reorder)
ELEM_POSITION = 0
ELEM_BLEND_WEIGHT = 1
ELEM_NORMAL = 2
ELEM_FOG_COORDINATE = 3
ELEM_PRIMARY_COLOR = 4
ELEM_SECONDARY_COLOR = 5
ELEM_BLEND_INDEX = 6
ELEM_TEXCOORD0 = 7   # diffuse UV
ELEM_TEXCOORD5 = 12  # bush vertex animation (3 floats)
ELEM_TEXCOORD7 = 14  # lightmap / baked paint UV
ELEM_TANGENT = 15

ELEM_NAMES = (
    'Position', 'BlendWeight', 'Normal', 'FogCoordinate', 'PrimaryColor',
    'SecondaryColor', 'BlendIndex', 'Texcoord0', 'Texcoord1', 'Texcoord2',
    'Texcoord3', 'Texcoord4', 'Texcoord5', 'Texcoord6', 'Texcoord7', 'Tangent',
)

# format id -> (numpy scalar type, component count, byte size)
# XYZ_Packed161616 is 8 bytes, not 6: 4 half floats with w as padding.
ELEM_FORMATS = {
    0: ('<f4', 1, 4),    # X_Float32
    1: ('<f4', 2, 8),    # XY_Float32
    2: ('<f4', 3, 12),   # XYZ_Float32
    3: ('<f4', 4, 16),   # XYZW_Float32
    4: ('u1', 4, 4),     # BGRA_Packed8888
    5: ('u1', 4, 4),     # ZYXW_Packed8888
    6: ('u1', 4, 4),     # RGBA_Packed8888
    7: ('<f2', 2, 4),    # XY_Packed1616
    8: ('<f2', 4, 8),    # XYZ_Packed161616 (+1 half padding)
    9: ('<f2', 4, 8),    # XYZW_Packed16161616
}


class MapGeoMesh:
    __slots__ = (
        'name', 'vertex_count', 'attrs', 'indices', 'submeshes',
        'layer', 'quality', 'disable_backface_culling',
        'bbox_min', 'bbox_max', 'matrix_raw',
        'render_flags', 'transition_behavior', 'visibility_controller_hash',
        'unk_v18_hash',
        'baked_light', 'stationary_light', 'baked_paint',
        'texture_overrides', 'texture_overrides_scale_bias',
        'point_light', 'light_probes', 'vertex_declarations',
    )

    def __init__(self):
        self.name = None
        self.vertex_count = 0
        self.attrs = {}              # semantic id -> (np.ndarray (n, comps), format id)
        self.indices = None          # np.ndarray u16, length = index_count
        self.submeshes = []          # dicts: name/hash/index_start/index_count/min_vertex/max_vertex
        self.layer = 0xFF
        self.quality = 0x1F
        self.disable_backface_culling = False
        self.bbox_min = (0.0, 0.0, 0.0)
        self.bbox_max = (0.0, 0.0, 0.0)
        self.matrix_raw = None       # tuple of 16 floats, file order
        self.render_flags = 0
        self.transition_behavior = 0  # ltk: VisibilityTransitionBehavior (v14+)
        self.visibility_controller_hash = 0
        self.unk_v18_hash = 0
        self.baked_light = None      # dict: path/scale/bias
        self.stationary_light = None
        self.baked_paint = None      # v12-16 channel; v17+ folds it into overrides
        self.texture_overrides = []  # list of dicts: index/path (v17+)
        self.texture_overrides_scale_bias = (1.0, 1.0, 0.0, 0.0)
        self.point_light = None
        self.light_probes = None
        self.vertex_declarations = []  # per stream: list of (name id, format id)


class MapGeo:
    __slots__ = ('path', 'version', 'header_texture_overrides', 'meshes',
                 'trailing_offset', 'use_separate_point_lights')

    def __init__(self):
        self.path = None
        self.version = 0
        self.header_texture_overrides = []  # dicts: index/path
        self.meshes = []
        self.trailing_offset = 0            # bucket grids + reflectors start here
        self.use_separate_point_lights = False


class _Cursor:
    __slots__ = ('data', 'pos')

    def __init__(self, data):
        self.data = data
        self.pos = 0

    def u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self):
        v = struct.unpack_from('<H', self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self):
        v = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self):
        v = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self, count=1):
        v = struct.unpack_from(f'<{count}f', self.data, self.pos)
        self.pos += 4 * count
        return v

    def string(self):
        n = self.u32()
        s = self.data[self.pos:self.pos + n].decode('ascii', errors='replace')
        self.pos += n
        return s

    def skip(self, n):
        self.pos += n


def _stream_dtype(elements):
    fields = []
    for k, (name_id, fmt_id) in enumerate(elements):
        np_type, comps, _ = ELEM_FORMATS[fmt_id]
        fields.append((f'e{k}', np_type, (comps,)))
    return np.dtype(fields)


def read_mapgeo(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()
    bs = _Cursor(data)

    mg = MapGeo()
    mg.path = filepath
    if data[:4] != MAPGEO_MAGIC:
        raise ValueError(f'Not a mapgeo file (bad magic): {filepath}')
    bs.skip(4)
    mg.version = bs.u32()
    if mg.version not in SUPPORTED_VERSIONS:
        raise ValueError(f'Unsupported mapgeo version {mg.version}: {filepath}')
    v = mg.version

    if v < 7:
        mg.use_separate_point_lights = bool(bs.u8())

    # Header (shader) texture overrides
    if v >= 17:
        for _ in range(bs.u32()):
            idx = bs.u32()
            mg.header_texture_overrides.append({'index': idx, 'path': bs.string()})
    elif v >= 9:
        mg.header_texture_overrides.append({'index': 0, 'path': bs.string()})
        if v >= 11:
            mg.header_texture_overrides.append({'index': 1, 'path': bs.string()})

    # Vertex declarations: 15 fixed element slots, element_count of them used
    declarations = []
    for _ in range(bs.u32()):
        bs.u32()  # usage (Static/Dynamic/Stream) — irrelevant for import
        element_count = bs.u32()
        elems = []
        for _ in range(element_count):
            elems.append((bs.u32(), bs.u32()))
        bs.skip(8 * (15 - element_count))
        declarations.append(elems)

    # Vertex buffers: record offsets, decode lazily once a mesh tells us the layout
    vb_spans = []
    for _ in range(bs.u32()):
        if v >= 13:
            bs.skip(1)  # per-buffer visibility byte, exporter rewrites it anyway
        size = bs.u32()
        vb_spans.append((bs.pos, size))
        bs.skip(size)

    ib_spans = []
    for _ in range(bs.u32()):
        if v >= 13:
            bs.skip(1)
        size = bs.u32()
        ib_spans.append((bs.pos, size))
        bs.skip(size)

    decoded_vbs = {}

    mesh_count = bs.u32()
    mg.meshes = [MapGeoMesh() for _ in range(mesh_count)]
    for mesh_id, mesh in enumerate(mg.meshes):
        mesh.name = bs.string() if v < 12 else f'MapGeo_Instance_{mesh_id}'

        mesh.vertex_count = bs.u32()
        vb_count = bs.u32()
        desc_id = bs.u32()
        vb_ids = [bs.i32() for _ in range(vb_count)]
        index_count = bs.u32()
        ib_id = bs.u32()

        # Decode this mesh's vertex streams (cached: buffers are 1:1 with meshes
        # in riot files, but the format technically allows sharing)
        for i, vb_id in enumerate(vb_ids):
            elems = declarations[desc_id + i]
            mesh.vertex_declarations.append(list(elems))
            if vb_id not in decoded_vbs:
                offset, size = vb_spans[vb_id]
                dt = _stream_dtype(elems)
                count = size // dt.itemsize
                decoded_vbs[vb_id] = np.frombuffer(data, dtype=dt, count=count, offset=offset)
            arr = decoded_vbs[vb_id]
            for k, (name_id, fmt_id) in enumerate(elems):
                mesh.attrs[name_id] = (arr[f'e{k}'][:mesh.vertex_count], fmt_id)

        ib_offset, ib_size = ib_spans[ib_id]
        mesh.indices = np.frombuffer(data, dtype='<u2', count=index_count, offset=ib_offset)

        if v >= 13:
            mesh.layer = bs.u8()
        if v >= 18:
            # New in v18, right after the layer byte. Zero on almost every mesh;
            # a unique hash on a handful (verified empirically on SR base_srx —
            # no reference tool supports 18 yet). Round-tripped, never interpreted.
            mesh.unk_v18_hash = bs.u32()
        if v >= 15:
            # Cluster-shared across mesh groups in v18 too, matching the v17
            # visibility-controller semantics — so the NEW field is the one above.
            mesh.visibility_controller_hash = bs.u32()

        for _ in range(bs.u32()):
            sm_hash = bs.u32()
            mesh.submeshes.append({
                'hash': sm_hash,
                'name': bs.string(),
                'index_start': bs.u32(),
                'index_count': bs.u32(),
                'min_vertex': bs.u32(),
                'max_vertex': bs.u32(),
            })

        if v != 5:
            mesh.disable_backface_culling = bool(bs.u8())

        mesh.bbox_min = bs.f32(3)
        mesh.bbox_max = bs.f32(3)
        mesh.matrix_raw = bs.f32(16)
        mesh.quality = bs.u8()

        if 7 <= v <= 12:
            mesh.layer = bs.u8()

        if v >= 11:
            if v < 14:
                mesh.render_flags = bs.u8()
            else:
                mesh.transition_behavior = bs.u8()
                mesh.render_flags = bs.u16() if v >= 16 else bs.u8()

        if v < 7 and mg.use_separate_point_lights:
            mesh.point_light = bs.f32(3)
        if v < 9:
            mesh.light_probes = bs.f32(27)

        mesh.baked_light = {'path': bs.string(), 'scale': bs.f32(2), 'bias': bs.f32(2)}
        if v >= 9:
            mesh.stationary_light = {'path': bs.string(), 'scale': bs.f32(2), 'bias': bs.f32(2)}
            if v >= 12:
                if v < 17:
                    mesh.baked_paint = {'path': bs.string(), 'scale': bs.f32(2), 'bias': bs.f32(2)}
                else:
                    for _ in range(bs.u32()):
                        idx = bs.u32()
                        mesh.texture_overrides.append({'index': idx, 'path': bs.string()})
                    mesh.texture_overrides_scale_bias = bs.f32(4)

    mg.trailing_offset = bs.pos
    return mg


def resolve_diffuse(mesh):
    """league-toolkit's resolve_diffuse_texture(): returns (path, uses_lightmap_uv,
    scale, bias) or None when the material default should be used (UV channel 0)."""
    for ov in mesh.texture_overrides:
        if ov['index'] == 0 and ov['path']:
            sb = mesh.texture_overrides_scale_bias
            return ov['path'], True, (sb[0], sb[1]), (sb[2], sb[3])
    if mesh.baked_paint and mesh.baked_paint['path']:
        return (mesh.baked_paint['path'], True,
                tuple(mesh.baked_paint['scale']), tuple(mesh.baked_paint['bias']))
    return None


# ---------------------------------------------------------------------------
# Blender scene construction
# ---------------------------------------------------------------------------

def _sanitize_material_name(name):
    if name == '-missing@environment-':
        return 'missing_environment'
    return name.replace('/', '__')


def _display_name(m, version, name_by_material):
    """Object name. Real names only exist in the file up to v11; for newer files
    the MapGeo_Instance_N placeholder can be swapped for the dominant material's
    last path segment. Object/mesh names are cosmetic — never written back — so
    paths are stripped from them; material names keep the full path (round-trip)."""
    if not name_by_material:
        return m.name
    if version < 12:
        return m.name.split('/')[-1] or m.name
    if not m.submeshes:
        return m.name
    dominant = max(m.submeshes, key=lambda sm: sm['index_count'])
    base = dominant['name'].split('/')[-1]
    if base.endswith('_MAT'):
        base = base[:-4]
    return base or m.name


def build_scene(operator, context, mg, collection_name=None, name_by_material=False):
    import bpy
    import mathutils
    from . import import_skl

    s = import_skl.IMPORT_SCALE
    # Same League -> Blender conversion as the rest of the addon: x -> -x,
    # league y (up) -> blender z, league z -> -y, uniform 0.01 scale.
    C = mathutils.Matrix((
        (-s, 0.0, 0.0, 0.0),
        (0.0, 0.0, -s, 0.0),
        (0.0, s, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ))
    C_inv = C.inverted()

    if collection_name is None:
        import os
        collection_name = os.path.splitext(os.path.basename(mg.path))[0]
    root = bpy.data.collections.new(collection_name)
    context.scene.collection.children.link(root)

    layer_cols = {}

    def layer_collection(bits):
        if bits in layer_cols:
            return layer_cols[bits]
        if bits == 0xFF:
            name = f'{collection_name}.AllLayers'
        else:
            on = [str(i + 1) for i in range(8) if bits & (1 << i)]
            name = f'{collection_name}.Layer{"_".join(on)}'
        col = bpy.data.collections.new(name)
        root.children.link(col)
        layer_cols[bits] = col
        return col

    raw_to_mat = {}  # raw (unsanitized) submesh name -> material

    def material_for(submesh_name):
        # Identity lives in mat['lol_path'] (full Riot path): Blender names cap at
        # 63 chars and the full paths truncate into collisions, so the name is
        # only the last path segment for display.
        if submesh_name in raw_to_mat:
            return raw_to_mat[submesh_name]
        mat = next((c for c in bpy.data.materials if c.get('lol_path') == submesh_name), None)
        if mat is None:
            sanitized = _sanitize_material_name(submesh_name)
            display = sanitized.split('__')[-1] or sanitized
            mat = bpy.data.materials.new(display)
            mat['lol_path'] = submesh_name
            mat.use_nodes = True
            mat.use_backface_culling = True
        raw_to_mat[submesh_name] = mat
        return mat

    built = []  # (blender object, MapGeoMesh)
    imported = 0
    for m in mg.meshes:
        pos_entry = m.attrs.get(ELEM_POSITION)
        if pos_entry is None or m.vertex_count == 0 or len(m.indices) == 0:
            continue

        verts = np.asarray(pos_entry[0], dtype=np.float32)[:, :3]
        # League -> Blender for mesh-local data: (x, y, z) -> (-x, -z, y) * scale
        verts_bl = np.empty_like(verts)
        verts_bl[:, 0] = -verts[:, 0] * s
        verts_bl[:, 1] = -verts[:, 2] * s
        verts_bl[:, 2] = verts[:, 1] * s

        tris = np.asarray(m.indices, dtype=np.int32).reshape(-1, 3)
        # The conversion mirrors (det < 0): flip winding so normals stay outward
        loop_verts = tris[:, [0, 2, 1]].ravel()
        tri_count = len(tris)

        obj_name = _display_name(m, mg.version, name_by_material)
        mesh = bpy.data.meshes.new(obj_name)
        mesh.vertices.add(len(verts_bl))
        mesh.vertices.foreach_set('co', verts_bl.ravel())
        mesh.loops.add(tri_count * 3)
        mesh.loops.foreach_set('vertex_index', loop_verts)
        mesh.polygons.add(tri_count)
        mesh.polygons.foreach_set('loop_start', np.arange(0, tri_count * 3, 3, dtype=np.int32))
        mesh.polygons.foreach_set('use_smooth', np.ones(tri_count, dtype=bool))

        # Submeshes -> material slots; faces are contiguous index ranges
        if m.submeshes:
            mat_indices = np.zeros(tri_count, dtype=np.int32)
            for slot, sm in enumerate(m.submeshes):
                mat = material_for(sm['name'])
                if m.disable_backface_culling:
                    mat.use_backface_culling = False
                mesh.materials.append(mat)
                face_start = sm['index_start'] // 3
                face_end = face_start + sm['index_count'] // 3
                mat_indices[face_start:face_end] = slot
            mesh.polygons.foreach_set('material_index', mat_indices)

        # UVs (V-flipped, like every other importer in the addon)
        tc0 = m.attrs.get(ELEM_TEXCOORD0)
        if tc0 is not None:
            uv = np.asarray(tc0[0], dtype=np.float32)[:, :2].copy()
            uv[:, 1] = 1.0 - uv[:, 1]
            uv_layer = mesh.uv_layers.new(name='UVMap')
            uv_layer.data.foreach_set('uv', uv[loop_verts].ravel())

        # Lightmap UV: raw Texcoord7 * baked_light scale + bias, then V-flip
        # (baked paint / texture overrides carry their own scale/bias — that one
        # is applied at material level, not baked into the UV layer)
        tc7 = m.attrs.get(ELEM_TEXCOORD7)
        if tc7 is not None:
            lm = np.asarray(tc7[0], dtype=np.float32)[:, :2].copy()
            scale, bias = (1.0, 1.0), (0.0, 0.0)
            if m.baked_light and m.baked_light['path']:
                scale, bias = tuple(m.baked_light['scale']), tuple(m.baked_light['bias'])
            lm[:, 0] = lm[:, 0] * scale[0] + bias[0]
            lm[:, 1] = 1.0 - (lm[:, 1] * scale[1] + bias[1])
            lm_layer = mesh.uv_layers.new(name='Lightmap')
            lm_layer.data.foreach_set('uv', lm[loop_verts].ravel())

        # Vertex colors
        col = m.attrs.get(ELEM_PRIMARY_COLOR)
        if col is not None:
            raw = np.asarray(col[0], dtype=np.float32) / 255.0
            rgba = raw[:, [2, 1, 0, 3]] if col[1] in (4, 5) else raw  # BGRA -> RGBA
            ca = mesh.color_attributes.new('Color', 'FLOAT_COLOR', 'POINT')
            ca.data.foreach_set('color', np.ascontiguousarray(rgba).ravel())

        # Bush vertex animation (Texcoord5, 3 floats) — kept for round-trip
        tc5 = m.attrs.get(ELEM_TEXCOORD5)
        if tc5 is not None and tc5[0].shape[1] >= 3:
            attr = mesh.attributes.new('Texcoord5', 'FLOAT_VECTOR', 'POINT')
            attr.data.foreach_set('vector',
                                  np.ascontiguousarray(tc5[0][:, :3], dtype=np.float32).ravel())

        mesh.update(calc_edges=True)

        # Custom split normals (same axis conversion, no scale)
        nrm = m.attrs.get(ELEM_NORMAL)
        if nrm is not None:
            n = np.asarray(nrm[0], dtype=np.float32)[:, :3]
            n_bl = np.empty_like(n)
            n_bl[:, 0] = -n[:, 0]
            n_bl[:, 1] = -n[:, 2]
            n_bl[:, 2] = n[:, 1]
            lens = np.linalg.norm(n_bl, axis=1, keepdims=True)
            np.divide(n_bl, lens, out=n_bl, where=lens > 1e-20)
            if hasattr(mesh, 'use_auto_smooth'):  # Blender 4.0; gone in 4.1+
                mesh.use_auto_smooth = True
            mesh.normals_split_custom_set_from_vertices(n_bl)

        obj = bpy.data.objects.new(obj_name, mesh)
        # File matrix is row-major with the translation in the last row
        # (D3D layout, verified against jade/brawl probes) -> transpose for mathutils.
        M = mathutils.Matrix((m.matrix_raw[0:4], m.matrix_raw[4:8],
                              m.matrix_raw[8:12], m.matrix_raw[12:16])).transposed()
        obj.matrix_world = C @ M @ C_inv

        # Round-trip data the scene itself can't represent
        obj['mapgeo'] = {
            'instance_name': m.name,
            'layer': m.layer,
            'quality': m.quality,
            'render_flags': m.render_flags,
            'transition_behavior': m.transition_behavior,
            # Hashes go in as hex strings: IDProperty ints are signed 32-bit and
            # these are full u32 values (export side: int(s, 16))
            'visibility_controller_hash': f'{m.visibility_controller_hash:08x}',
            'unk_v18_hash': f'{m.unk_v18_hash:08x}',
            'disable_backface_culling': m.disable_backface_culling,
            'baked_light': m.baked_light or {},
            'stationary_light': m.stationary_light or {},
            'baked_paint': m.baked_paint or {},
            'texture_overrides': m.texture_overrides,
            'texture_overrides_scale_bias': list(m.texture_overrides_scale_bias),
            'vertex_declarations': [[list(e) for e in d] for d in m.vertex_declarations],
        }
        layer_collection(m.layer if mg.version >= 7 else 0xFF).objects.link(obj)
        built.append((obj, m))
        imported += 1

    context.scene['mapgeo_version'] = mg.version
    context.scene['mapgeo_source'] = mg.path
    context.scene['mapgeo_trailing_offset'] = mg.trailing_offset
    context.scene['mapgeo_header_texture_overrides'] = mg.header_texture_overrides

    return {'imported': imported, 'objects': built, 'materials': raw_to_mat}


# ---------------------------------------------------------------------------
# Textures (M2): materials.bin via native bin_parser.dll + .tex via tex_converter
# ---------------------------------------------------------------------------

def _fnv1a(s):
    h = 0x811c9dc5
    for c in s.lower():
        h = ((h ^ ord(c)) * 0x01000193) & 0xFFFFFFFF
    return h


def _find_materials_bin(mapgeo_path):
    import os
    folder = os.path.dirname(mapgeo_path)
    stem = os.path.splitext(os.path.basename(mapgeo_path))[0]
    exact = os.path.join(folder, f'{stem}.materials.bin')
    if os.path.exists(exact):
        return exact
    import glob
    candidates = glob.glob(os.path.join(folder, '*.materials.bin'))
    return candidates[0] if len(candidates) == 1 else None


def _resolve_asset(mapgeo_path, asset_path):
    """Map an 'ASSETS/...' path from the bin onto disk, relative to the extract
    root (the folder that contains data/ in the mapgeo's own path)."""
    import os
    if not asset_path:
        return None
    parts = os.path.normpath(mapgeo_path).split(os.sep)
    root = None
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].lower() == 'data':
            root = os.sep.join(parts[:i])
            break
    if root is None:
        root = os.path.dirname(mapgeo_path)
    rel = asset_path.replace('\\', '/').lstrip('/')
    candidate = os.path.join(root, rel.replace('/', os.sep))
    if os.path.exists(candidate):
        return candidate
    base, ext = os.path.splitext(candidate)
    for other in ('.tex', '.dds', '.png'):
        if other != ext.lower() and os.path.exists(base + other):
            return base + other
    return None


def _pick_diffuse(samplers):
    """Sampler naming varies per shader (DiffuseTexture / Diffuse_Texture / ...);
    the native parser returns all of them and the choice happens here."""
    fallback = None
    for s in samplers:
        n = s['name'].lower()
        if n in ('diffusetexture', 'diffuse_texture', 'diffuse'):
            return s['texture']
        if fallback is None and 'diffuse' in n:
            fallback = s['texture']
    if fallback:
        return fallback
    for s in samplers:
        n = s['name'].lower()
        if 'albedo' in n or 'basecolor' in n or 'color_tex' in n:
            return s['texture']
    return None


def _load_image(disk_path, cache):
    """Load a .tex/.dds as a packed Blender image. Returns None on failure."""
    import bpy
    import os
    if disk_path in cache:
        return cache[disk_path]
    img = None
    tmp = None
    try:
        load_path = disk_path
        if disk_path.lower().endswith('.tex'):
            import tempfile
            from ..utils import texture_manager
            dds_bytes = texture_manager.tex_to_dds_bytes(disk_path)
            fd, tmp = tempfile.mkstemp(suffix='.dds')
            os.close(fd)
            with open(tmp, 'wb') as f:
                f.write(dds_bytes)
            load_path = tmp
        img = bpy.data.images.load(load_path, check_existing=False)
        img.pack()  # embed before the temp file disappears
        img.name = os.path.basename(disk_path)
        img['lol_source_path'] = disk_path
    except Exception as e:
        print(f'Aventurine: failed to load map texture {disk_path}: {e}')
        img = None
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    cache[disk_path] = img
    return img


def _wire_diffuse(mat, img, uv_layer_name):
    """Idempotently point the material's diffuse at img sampled via uv_layer_name."""
    if not mat.use_nodes or mat.node_tree is None:
        mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        nodes.clear()
        out = nodes.new('ShaderNodeOutputMaterial')
        out.location = (300, 0)
        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
        links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])

    tex = nodes.get('LOL Diffuse')
    if tex is None:
        tex = nodes.new('ShaderNodeTexImage')
        tex.name = tex.label = 'LOL Diffuse'
        tex.location = (-400, 100)
    tex.image = img

    uv = nodes.get('LOL UV')
    if uv is None:
        uv = nodes.new('ShaderNodeUVMap')
        uv.name = uv.label = 'LOL UV'
        uv.location = (-600, 0)
    uv.uv_map = uv_layer_name

    links.new(uv.outputs['UV'], tex.inputs['Vector'])
    if not bsdf.inputs['Base Color'].is_linked:
        links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    if not bsdf.inputs['Alpha'].is_linked:
        links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
    bsdf.inputs['Roughness'].default_value = 1.0
    if hasattr(mat, 'blend_method'):
        mat.blend_method = 'CLIP'
    return True


def _ensure_paint_uv(obj, m, scale, bias):
    """UV layer for baked-paint / texture-override sampling (UV channel 1 with the
    override's own scale/bias). Reuses 'Lightmap' when the transform matches the
    baked-light one it was built with; otherwise builds a dedicated layer."""
    mesh = obj.data
    bl = m.baked_light if (m.baked_light and m.baked_light['path']) else None
    bl_scale = tuple(bl['scale']) if bl else (1.0, 1.0)
    bl_bias = tuple(bl['bias']) if bl else (0.0, 0.0)
    if mesh.uv_layers.get('Lightmap') and \
       all(abs(a - b) < 1e-6 for a, b in zip(scale + bias, bl_scale + bl_bias)):
        return 'Lightmap'

    tc7 = m.attrs.get(ELEM_TEXCOORD7)
    if tc7 is None:
        return 'Lightmap' if mesh.uv_layers.get('Lightmap') else 'UVMap'
    existing = mesh.uv_layers.get('PaintUV')
    if existing is not None:
        return 'PaintUV'

    lm = np.asarray(tc7[0], dtype=np.float32)[:, :2].copy()
    lm[:, 0] = lm[:, 0] * scale[0] + bias[0]
    lm[:, 1] = 1.0 - (lm[:, 1] * scale[1] + bias[1])
    loop_verts = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get('vertex_index', loop_verts)
    layer = mesh.uv_layers.new(name='PaintUV')
    layer.data.foreach_set('uv', lm[loop_verts].ravel())
    return 'PaintUV'


def apply_textures(operator, context, mg, build_result, filepath):
    """Texture every imported material: per-mesh override chain first (texture
    override sampler 0 (v17+) -> baked paint channel (v12-16)), then the
    material's default diffuse from materials.bin. Missing files degrade to
    untextured slots, never a failed import."""
    import bpy
    from ..utils import texture_manager

    bin_path = _find_materials_bin(filepath)
    doc = texture_manager.parse_materials_bin(bin_path) if bin_path else None
    if bin_path and doc is None:
        operator.report({'WARNING'},
                        'bin_parser.dll missing or outdated — material defaults skipped '
                        '(rebuild native/bin_parser.dll)')

    by_key = {entry['key']: entry for entry in doc.get('materials', [])} if doc else {}
    cache = {}
    stats = {'textured': 0, 'missing_files': 0, 'no_entry': 0, 'has_bin': doc is not None}

    # Shared materials: default diffuse from materials.bin (UV channel 0)
    for raw_name, mat in build_result['materials'].items():
        entry = by_key.get(f'{_fnv1a(raw_name):08x}')
        if entry is None:
            if doc is not None:
                stats['no_entry'] += 1
            continue
        asset = _pick_diffuse(entry['samplers'])
        if not asset:
            continue
        disk = _resolve_asset(filepath, asset)
        if disk is None:
            stats['missing_files'] += 1
            continue
        img = _load_image(disk, cache)
        if img is not None:
            _wire_diffuse(mat, img, 'UVMap')
            stats['textured'] += 1

    # Per-mesh overrides replace the diffuse on that mesh only -> material variants.
    # These textures come from the mapgeo itself, so this works without materials.bin.
    variants = {}
    for obj, m in build_result['objects']:
        resolved = resolve_diffuse(m)
        if resolved is None:
            continue
        asset, _, scale, bias = resolved
        disk = _resolve_asset(filepath, asset)
        if disk is None:
            stats['missing_files'] += 1
            continue
        img = _load_image(disk, cache)
        if img is None:
            continue
        uv_name = _ensure_paint_uv(obj, m, scale, bias)
        for slot in obj.material_slots:
            if slot.material is None:
                continue
            key = (slot.material.name.split('.')[0], img.name, uv_name)
            if key not in variants:
                variant = slot.material.copy()
                variant['lol_variant'] = True  # same league material, mesh-local texture
                _wire_diffuse(variant, img, uv_name)
                variants[key] = variant
            slot.material = variants[key]
            stats['textured'] += 1

    return stats


def load(operator, context, filepath, name_by_material=False, import_textures=True):
    try:
        mg = read_mapgeo(filepath)
    except ValueError as e:
        operator.report({'ERROR'}, str(e))
        return {'CANCELLED'}

    result = build_scene(operator, context, mg, name_by_material=name_by_material)
    msg = f"Imported {result['imported']} map meshes (mapgeo v{mg.version})"

    if import_textures:
        stats = apply_textures(operator, context, mg, result, filepath)
        msg += f"; {stats['textured']} materials textured"
        if not stats['has_bin']:
            msg += ' (no materials.bin next to the map)'
        if stats['missing_files']:
            msg += (f", {stats['missing_files']} texture files not found "
                    f"(extract the map's assets folder too)")
        if stats['no_entry']:
            msg += f", {stats['no_entry']} without materials.bin entry"

    operator.report({'INFO'}, msg)
    return {'FINISHED'}


if __name__ == '__main__':
    # Standalone parse check: python import_mapgeo.py <file.mapgeo>
    import sys
    import time
    t0 = time.time()
    mg = read_mapgeo(sys.argv[1])
    dt = time.time() - t0
    total_v = sum(m.vertex_count for m in mg.meshes)
    total_i = sum(len(m.indices) for m in mg.meshes)
    print(f'version={mg.version} meshes={len(mg.meshes)} verts={total_v} indices={total_i}')
    print(f'header_overrides={mg.header_texture_overrides}')
    print(f'trailing_offset={mg.trailing_offset} (file end at {mg.trailing_offset} + trailing blob)')
    print(f'parse time: {dt:.2f}s')
