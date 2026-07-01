import struct

import numpy as np

from . import import_mapgeo as mg

_SUPPORTED_ELEMENTS = {
    mg.ELEM_POSITION, mg.ELEM_NORMAL, mg.ELEM_PRIMARY_COLOR,
    mg.ELEM_TEXCOORD0, mg.ELEM_TEXCOORD5, mg.ELEM_TEXCOORD7,
}

_DEFAULT_ELEMENT_ORDER = (
    (mg.ELEM_POSITION, 2),
    (mg.ELEM_NORMAL, 2),
    (mg.ELEM_PRIMARY_COLOR, 6),
    (mg.ELEM_TEXCOORD0, 1),
    (mg.ELEM_TEXCOORD5, 2),
    (mg.ELEM_TEXCOORD7, 1),
)


class _Writer:
    __slots__ = ('chunks',)

    def __init__(self):
        self.chunks = []

    def raw(self, data):
        self.chunks.append(bytes(data))

    def u8(self, v):
        self.chunks.append(struct.pack('<B', v))

    def u16(self, v):
        self.chunks.append(struct.pack('<H', v))

    def u32(self, v):
        self.chunks.append(struct.pack('<I', v))

    def i32(self, v):
        self.chunks.append(struct.pack('<i', v))

    def f32(self, *values):
        self.chunks.append(struct.pack(f'<{len(values)}f', *values))

    def bool_(self, v):
        self.chunks.append(struct.pack('<B', 1 if v else 0))

    def string(self, s):
        try:
            enc = s.encode('ascii')
        except UnicodeEncodeError as e:
            raise ValueError(f'name {s!r} has non-ASCII characters; mapgeo strings must be ASCII') from e
        self.u32(len(enc))
        self.chunks.append(enc)

    def getvalue(self):
        return b''.join(self.chunks)


def _mapgeo_prop(obj, key, default=None):
    data = obj.get('mapgeo')
    if data is None:
        return default
    value = data.get(key, default)
    return value if value is not None else default


def _asset_channel(data):
    if not data:
        return '', (1.0, 1.0), (0.0, 0.0)
    path = data.get('path') or ''
    scale = tuple(data.get('scale', (1.0, 1.0)))
    bias = tuple(data.get('bias', (0.0, 0.0)))
    return path, scale, bias


def _to_league_position(verts_bl, s):
    v = np.asarray(verts_bl, dtype=np.float32)
    out = np.empty_like(v)
    out[:, 0] = -v[:, 0] / s
    out[:, 1] = v[:, 2] / s
    out[:, 2] = -v[:, 1] / s
    return out


def _to_league_normal(normals_bl):
    n = np.asarray(normals_bl, dtype=np.float32)
    out = np.empty_like(n)
    out[:, 0] = -n[:, 0]
    out[:, 1] = n[:, 2]
    out[:, 2] = -n[:, 1]
    lens = np.linalg.norm(out, axis=1, keepdims=True)
    np.divide(out, lens, out=out, where=lens > 1e-20)
    return out


def _to_league_uv(uv_bl):
    uv = np.array(uv_bl, dtype=np.float32, copy=True)
    uv[:, 1] = 1.0 - uv[:, 1]
    return uv


def _to_league_lightmap(uv_bl, scale, bias):
    uv = np.array(uv_bl, dtype=np.float32, copy=True)
    out = np.empty_like(uv)
    sx = scale[0] if scale[0] else 1.0
    sy = scale[1] if scale[1] else 1.0
    out[:, 0] = (uv[:, 0] - bias[0]) / sx
    out[:, 1] = ((1.0 - uv[:, 1]) - bias[1]) / sy
    return out


def _matrix_to_file(obj, s):
    import mathutils

    C = mathutils.Matrix((
        (-s, 0.0, 0.0, 0.0),
        (0.0, 0.0, -s, 0.0),
        (0.0, s, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ))
    file_rows = (C.inverted() @ obj.matrix_world @ C).transposed()
    return [component for row in file_rows for component in row]


def _read_uv_layer(mesh, name):
    layer = mesh.uv_layers.get(name)
    if layer is None:
        return None
    data = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    layer.data.foreach_get('uv', data)
    return data.reshape(-1, 2)


def _gather_positions(mesh):
    data = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', data)
    return data.reshape(-1, 3)


def _gather_normals(mesh):
    data = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('normal', data)
    return data.reshape(-1, 3)


def _gather_point_color(mesh, name):
    attr = mesh.color_attributes.get(name)
    if attr is None or attr.domain != 'POINT':
        return None
    data = np.empty(len(mesh.vertices) * 4, dtype=np.float32)
    attr.data.foreach_get('color', data)
    return data.reshape(-1, 4)


def _gather_point_vector(mesh, name):
    attr = mesh.attributes.get(name)
    if attr is None or attr.domain != 'POINT' or attr.data_type != 'FLOAT_VECTOR':
        return None
    data = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    attr.data.foreach_get('vector', data)
    return data.reshape(-1, 3)


def _build_mesh_geometry(mesh):
    mesh.calc_loop_triangles()
    tri_count = len(mesh.loop_triangles)
    if tri_count == 0 or len(mesh.vertices) == 0:
        return None

    loop_vidx = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get('vertex_index', loop_vidx)

    tri_loops = np.empty(tri_count * 3, dtype=np.int64)
    mesh.loop_triangles.foreach_get('loops', tri_loops)

    tri_polys = np.empty(tri_count, dtype=np.int64)
    mesh.loop_triangles.foreach_get('polygon_index', tri_polys)
    poly_mat = np.zeros(len(mesh.polygons), dtype=np.int64)
    if len(mesh.polygons):
        mesh.polygons.foreach_get('material_index', poly_mat)
    tri_mat = poly_mat[tri_polys]

    order = np.argsort(tri_mat, kind='stable')
    tri_loops = tri_loops.reshape(-1, 3)[order].ravel()
    tri_mat = tri_mat[order]

    uv0 = _read_uv_layer(mesh, 'UVMap')
    lightmap_uv = _read_uv_layer(mesh, 'PaintUV')
    if lightmap_uv is None:
        lightmap_uv = _read_uv_layer(mesh, 'Lightmap')

    key_cols = [loop_vidx[tri_loops].reshape(-1, 1)]
    if uv0 is not None:
        key_cols.append(np.round(uv0[tri_loops] * 100000.0).astype(np.int64))
    if lightmap_uv is not None:
        key_cols.append(np.round(lightmap_uv[tri_loops] * 100000.0).astype(np.int64))
    keys = np.concatenate(key_cols, axis=1)

    unique_rows, first_indices, new_ids = np.unique(
        keys, axis=0, return_index=True, return_inverse=True)

    src_vertex_of_new = unique_rows[:, 0]
    first_loop_of_new = tri_loops[first_indices]
    new_tris = new_ids.reshape(-1, 3).astype(np.int64)
    file_tris = new_tris[:, [0, 2, 1]]

    return new_tris, file_tris, src_vertex_of_new, first_loop_of_new, uv0, lightmap_uv, tri_mat


def _build_submeshes(mesh, tri_mat, new_tris):
    boundaries = np.flatnonzero(np.diff(tri_mat)) + 1
    starts = np.concatenate(([0], boundaries)).astype(np.int64)
    ends = np.concatenate((boundaries, [len(tri_mat)])).astype(np.int64)
    submeshes = []
    for start, end in zip(starts, ends):
        mat_index = int(tri_mat[start])
        material = mesh.materials[mat_index] if 0 <= mat_index < len(mesh.materials) else None
        name = (material.get('lol_path') if material else None) or \
               (material.name if material else 'Default')
        verts = new_tris[start:end]
        submeshes.append({
            'hash': mg._fnv1a(name),
            'name': name,
            'index_start': int(start) * 3,
            'index_count': int(end - start) * 3,
            'min_vertex': int(verts.min()),
            'max_vertex': int(verts.max()),
        })
    return submeshes


def _default_declaration(available):
    return [(name_id, fmt_id) for name_id, fmt_id in _DEFAULT_ELEMENT_ORDER if name_id in available]


def _resolve_declaration(obj, available):
    cached = _mapgeo_prop(obj, 'vertex_declarations')
    if not cached:
        return [_default_declaration(available)]
    streams = []
    for stream in cached:
        elems = []
        for name_id_raw, fmt_id_raw in stream:
            name_id = int(name_id_raw)
            if name_id not in _SUPPORTED_ELEMENTS or name_id not in available:
                raise ValueError(
                    f"{obj.name}: can't reproduce vertex element {mg.ELEM_NAMES[name_id]} on "
                    "export; re-add that data to the mesh, or delete the object's cached "
                    "'mapgeo' vertex_declarations to fall back to a default layout")
            elems.append((name_id, int(fmt_id_raw)))
        streams.append(elems)
    return streams


def _encode_vertex_buffer(elements, sources, n):
    dt = mg._stream_dtype(elements)
    arr = np.zeros(n, dtype=dt)
    for k, (name_id, fmt_id) in enumerate(elements):
        field = f'e{k}'
        _, comps, _ = mg.ELEM_FORMATS[fmt_id]
        src = np.asarray(sources[name_id], dtype=np.float32)
        if name_id == mg.ELEM_PRIMARY_COLOR:
            u8 = np.round(np.clip(src, 0.0, 1.0) * 255.0).astype(np.uint8)
            if fmt_id in (4, 5):
                u8 = u8[:, [2, 1, 0, 3]]
            arr[field] = u8[:, :comps]
        else:
            width = min(comps, src.shape[1])
            arr[field][:, :width] = src[:, :width]
    return arr.tobytes()


def _build_model(obj, s):
    mesh = obj.data
    geometry = _build_mesh_geometry(mesh)
    if geometry is None:
        return None
    new_tris, file_tris, src_vertex_of_new, first_loop_of_new, uv0, lightmap_uv, tri_mat = geometry

    positions_bl = _gather_positions(mesh)[src_vertex_of_new]
    normals_bl = _gather_normals(mesh)[src_vertex_of_new]
    color = _gather_point_color(mesh, 'Color')
    tc5 = _gather_point_vector(mesh, 'Texcoord5')

    baked_light = _asset_channel(_mapgeo_prop(obj, 'baked_light'))
    stationary_light = _asset_channel(_mapgeo_prop(obj, 'stationary_light'))
    baked_paint_raw = _mapgeo_prop(obj, 'baked_paint')
    baked_paint = _asset_channel(baked_paint_raw) if baked_paint_raw else None
    texture_overrides = [
        {'index': int(ov['index']), 'path': ov['path']}
        for ov in (_mapgeo_prop(obj, 'texture_overrides', []) or [])
    ]
    scale_bias = tuple(_mapgeo_prop(obj, 'texture_overrides_scale_bias', (1.0, 1.0, 0.0, 0.0)))

    sources = {
        mg.ELEM_POSITION: _to_league_position(positions_bl, s),
        mg.ELEM_NORMAL: _to_league_normal(normals_bl),
    }
    available = {mg.ELEM_POSITION, mg.ELEM_NORMAL}
    if uv0 is not None:
        sources[mg.ELEM_TEXCOORD0] = _to_league_uv(uv0[first_loop_of_new])
        available.add(mg.ELEM_TEXCOORD0)
    if lightmap_uv is not None:
        sources[mg.ELEM_TEXCOORD7] = _to_league_lightmap(
            lightmap_uv[first_loop_of_new], scale_bias[0:2], scale_bias[2:4])
        available.add(mg.ELEM_TEXCOORD7)
    if color is not None:
        sources[mg.ELEM_PRIMARY_COLOR] = color[src_vertex_of_new]
        available.add(mg.ELEM_PRIMARY_COLOR)
    if tc5 is not None:
        sources[mg.ELEM_TEXCOORD5] = tc5[src_vertex_of_new]
        available.add(mg.ELEM_TEXCOORD5)

    streams = _resolve_declaration(obj, available)
    n = len(src_vertex_of_new)
    vertex_buffers = [_encode_vertex_buffer(elems, sources, n) for elems in streams]

    positions_league = sources[mg.ELEM_POSITION]

    hash_str = _mapgeo_prop(obj, 'visibility_controller_hash', '0')
    unk_v18_str = _mapgeo_prop(obj, 'unk_v18_hash', '0')

    return {
        'name': _mapgeo_prop(obj, 'instance_name', obj.name),
        'vertex_count': n,
        'streams': streams,
        'vertex_buffers': vertex_buffers,
        'indices': file_tris.astype('<u2'),
        'submeshes': _build_submeshes(mesh, tri_mat, new_tris),
        'layer': int(_mapgeo_prop(obj, 'layer', 0xFF)) & 0xFF,
        'quality': int(_mapgeo_prop(obj, 'quality', 0x1F)) & 0xFF,
        'render_flags': int(_mapgeo_prop(obj, 'render_flags', 0)),
        'transition_behavior': int(_mapgeo_prop(obj, 'transition_behavior', 0)) & 0xFF,
        'visibility_controller_hash': int(str(hash_str), 16),
        'unk_v18_hash': int(str(unk_v18_str), 16),
        'disable_backface_culling': bool(_mapgeo_prop(obj, 'disable_backface_culling', False)),
        'bbox_min': positions_league.min(axis=0).tolist(),
        'bbox_max': positions_league.max(axis=0).tolist(),
        'matrix': _matrix_to_file(obj, s),
        'baked_light': baked_light,
        'stationary_light': stationary_light,
        'baked_paint': baked_paint,
        'texture_overrides': texture_overrides,
        'texture_overrides_scale_bias': scale_bias,
    }


def _write_channel(writer, channel):
    path, scale, bias = channel
    writer.string(path or '')
    writer.f32(scale[0], scale[1])
    writer.f32(bias[0], bias[1])


def _write_header(writer, version, separate_point_lights, header_overrides):
    writer.raw(mg.MAPGEO_MAGIC)
    writer.u32(version)
    if version < 7:
        writer.bool_(separate_point_lights)

    if version >= 17:
        writer.u32(len(header_overrides))
        for ov in header_overrides:
            writer.u32(int(ov['index']))
            writer.string(ov['path'])
    elif version >= 9:
        by_index = {int(ov['index']): ov['path'] for ov in header_overrides}
        writer.string(by_index.get(0, ''))
        if version >= 11:
            writer.string(by_index.get(1, ''))


def _write_vertex_description(writer, elements):
    writer.u32(0)
    writer.u32(len(elements))
    for name_id, fmt_id in elements:
        writer.u32(name_id)
        writer.u32(fmt_id)
    for _ in range(15 - len(elements)):
        writer.u32(mg.ELEM_POSITION)
        writer.u32(3)


def _write_model(writer, model, version, vb_ids, desc_id, ib_id, separate_point_lights):
    if version < 12:
        writer.string(model['name'])

    writer.u32(model['vertex_count'])
    writer.u32(len(vb_ids))
    writer.u32(desc_id)
    for vb_id in vb_ids:
        writer.i32(vb_id)

    writer.u32(len(model['indices']))
    writer.i32(ib_id)

    if version >= 13:
        writer.u8(model['layer'])
    if version >= 18:
        writer.u32(model['unk_v18_hash'])
    if version >= 15:
        writer.u32(model['visibility_controller_hash'])

    writer.u32(len(model['submeshes']))
    for sm in model['submeshes']:
        writer.u32(sm['hash'])
        writer.string(sm['name'])
        writer.u32(sm['index_start'])
        writer.u32(sm['index_count'])
        writer.u32(sm['min_vertex'])
        writer.u32(sm['max_vertex'])

    if version != 5:
        writer.bool_(model['disable_backface_culling'])

    writer.f32(*model['bbox_min'])
    writer.f32(*model['bbox_max'])
    writer.f32(*model['matrix'])

    writer.u8(model['quality'])

    if 7 <= version <= 12:
        writer.u8(model['layer'])

    if 11 <= version < 14:
        writer.u8(model['render_flags'] & 0xFF)
    elif version >= 14:
        writer.u8(model['transition_behavior'])
        if version >= 16:
            writer.u16(model['render_flags'] & 0xFFFF)
        else:
            writer.u8(model['render_flags'] & 0xFF)

    if version < 7 and separate_point_lights:
        writer.f32(0.0, 0.0, 0.0)

    if version < 9:
        for _ in range(9):
            writer.f32(0.0, 0.0, 0.0)
        _write_channel(writer, model['baked_light'])
        return

    _write_channel(writer, model['baked_light'])
    _write_channel(writer, model['stationary_light'])

    if version >= 17:
        writer.u32(len(model['texture_overrides']))
        for ov in model['texture_overrides']:
            writer.u32(ov['index'])
            writer.string(ov['path'])
        writer.f32(*model['texture_overrides_scale_bias'])
    elif version >= 12:
        _write_channel(writer, model['baked_paint'] or ('', (1.0, 1.0), (0.0, 0.0)))


def _write_scene_graphs(writer, version, bbox_min, bbox_max):
    if version >= 15:
        writer.u32(1)
        writer.u32(0)
    if version >= 18:
        writer.f32(0.0)
    writer.f32(float(bbox_min[0]), float(bbox_min[2]), float(bbox_max[0]), float(bbox_max[2]))
    writer.f32(0.0, 0.0)
    writer.f32(0.0, 0.0)
    writer.u16(0)
    writer.bool_(True)
    writer.u8(0)
    writer.u32(0)
    writer.u32(0)


def _write_planar_reflectors(writer, version):
    if version < 13:
        return
    writer.u32(0)


def _collect_mesh_objects(collection):
    objects = []
    seen = set()

    def walk(col):
        for obj in col.objects:
            if obj.type == 'MESH' and obj.name not in seen:
                seen.add(obj.name)
                objects.append(obj)
        for child in col.children:
            walk(child)

    walk(collection)
    return objects


def write_mapgeo(context, collection, version, filepath):
    from . import import_skl

    s = import_skl.IMPORT_SCALE
    header_overrides = list(context.scene.get('mapgeo_header_texture_overrides', []) or [])
    separate_point_lights = version < 7 and bool(context.scene.get('mapgeo_separate_point_lights', False))

    objects = _collect_mesh_objects(collection)
    models = [m for m in (_build_model(obj, s) for obj in objects) if m is not None]
    if not models:
        raise ValueError('No exportable mesh objects found in the target collection')

    writer = _Writer()
    _write_header(writer, version, separate_point_lights, header_overrides)

    declarations = []
    vertex_buffers = []
    index_buffers = []
    model_bufs = []
    for model in models:
        desc_id = len(declarations)
        vb_ids = []
        for elems, vb_bytes in zip(model['streams'], model['vertex_buffers']):
            declarations.append(elems)
            vb_ids.append(len(vertex_buffers))
            vertex_buffers.append((model['layer'], vb_bytes))
        ib_id = len(index_buffers)
        index_buffers.append((model['layer'], model['indices'].tobytes()))
        model_bufs.append((desc_id, vb_ids, ib_id))

    writer.u32(len(declarations))
    for elems in declarations:
        _write_vertex_description(writer, elems)

    writer.u32(len(vertex_buffers))
    for layer, data in vertex_buffers:
        if version >= 13:
            writer.u8(layer)
        writer.u32(len(data))
        writer.raw(data)

    writer.u32(len(index_buffers))
    for layer, data in index_buffers:
        if version >= 13:
            writer.u8(layer)
        writer.u32(len(data))
        writer.raw(data)

    writer.u32(len(models))
    for model, (desc_id, vb_ids, ib_id) in zip(models, model_bufs):
        _write_model(writer, model, version, vb_ids, desc_id, ib_id, separate_point_lights)

    all_min = np.min([m['bbox_min'] for m in models], axis=0)
    all_max = np.max([m['bbox_max'] for m in models], axis=0)
    _write_scene_graphs(writer, version, all_min, all_max)
    _write_planar_reflectors(writer, version)

    with open(filepath, 'wb') as f:
        f.write(writer.getvalue())

    return len(models)


def save(operator, context, filepath, collection_name=None, version=None):
    import bpy

    collection = bpy.data.collections.get(collection_name) if collection_name else None
    if collection is None:
        collection = context.view_layer.active_layer_collection.collection

    scene_version = context.scene.get('mapgeo_version')
    export_version = version if version else (scene_version if scene_version else 18)
    if export_version not in mg.SUPPORTED_VERSIONS:
        operator.report({'ERROR'}, f'Unsupported mapgeo version {export_version}')
        return {'CANCELLED'}

    try:
        count = write_mapgeo(context, collection, export_version, filepath)
    except ValueError as e:
        operator.report({'ERROR'}, str(e))
        return {'CANCELLED'}

    operator.report(
        {'INFO'},
        f"Exported {count} map meshes to mapgeo v{export_version} from collection "
        f"'{collection.name}'. Bucket-grid spatial culling is written disabled (all "
        "geometry always renders) since this addon doesn't reconstruct it yet.")
    return {'FINISHED'}
