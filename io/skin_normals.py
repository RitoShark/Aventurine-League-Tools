"""Keep SKN shading normals separate from Blender's flat viewport shading."""
from contextlib import contextmanager
from functools import wraps
import hashlib
import math
import struct
import bpy

ATTRIBUTE = 'SKNNormal'
BASIS = 'lol_skn_normal_basis'
ACTIVE = 'lol_skn_export_normals_active'


def geometry_signature(mesh):
    digest = hashlib.sha256()
    for vertex in mesh.vertices:
        digest.update(struct.pack('<3f', *vertex.co))
    for poly in mesh.polygons:
        digest.update(struct.pack('<II', poly.loop_start, poly.loop_total))
    for loop in mesh.loops:
        digest.update(struct.pack('<I', loop.vertex_index))
    return digest.hexdigest()


def store(mesh, normals):
    """Store Blender-space corner normals without enabling custom shading."""
    if len(normals) != len(mesh.loops):
        raise ValueError('SKN normal count does not match mesh corners')
    attribute = mesh.attributes.get(ATTRIBUTE)
    if attribute is None:
        attribute = mesh.attributes.new(ATTRIBUTE, 'FLOAT_VECTOR', 'CORNER')
    if attribute.domain != 'CORNER' or attribute.data_type != 'FLOAT_VECTOR':
        raise ValueError('SKNNormal must be a face-corner vector attribute')
    attribute.data.foreach_set('vector', [v for normal in normals for v in normal])
    mesh[BASIS] = geometry_signature(mesh)


def preserved(mesh):
    # Explicitly authored Blender custom normals override the stored import.
    if mesh.has_custom_normals and not mesh.get(ACTIVE):
        return None
    attribute = mesh.attributes.get(ATTRIBUTE)
    if (attribute is None or attribute.domain != 'CORNER'
            or attribute.data_type != 'FLOAT_VECTOR'
            or len(attribute.data) != len(mesh.loops)
            or mesh.get(BASIS) != geometry_signature(mesh)):
        return None
    return [item.vector.copy() for item in attribute.data]


def prepare_export_mesh(mesh, *, imported=False):
    """Prepare a disposable mesh for file normals, not flat preview normals.

    Edits invalidate imported vectors, but must not turn the preview's flat
    faces into a hard seam on every triangle. Recalculate smooth normals on
    the edited geometry, retaining marked sharp edges and explicit custom
    normals. Native Blender meshes keep their authored shading.
    """
    normals = preserved(mesh)
    if normals is None and not (imported or BASIS in mesh):
        return False
    mesh[ACTIVE] = normals is not None
    for poly in mesh.polygons:
        poly.use_smooth = True
    if hasattr(mesh, 'use_auto_smooth'):
        # Enabling Blender 4.0's default angle threshold would introduce new
        # seams. Keep existing smooth settings, enabling sharp-edge evaluation
        # only when the mesh actually has an authored sharp edge.
        if normals is not None or mesh.has_custom_normals or any(edge.use_edge_sharp for edge in mesh.edges):
            if not mesh.use_auto_smooth:
                mesh.auto_smooth_angle = math.pi
            mesh.use_auto_smooth = True
    if normals is not None:
        mesh.normals_split_custom_set(normals)
    mesh.update()
    return True


@contextmanager
def export_meshes(objects):
    """Prepare file normals on disposable mesh copies for modifier evaluation.

    Original meshes, shading and selection remain unchanged, including on error.
    This also makes tangent recalculation use the normals written to the SKN.
    """
    copies = []
    seen = set()
    try:
        for obj in objects:
            if obj.type != 'MESH' or obj.as_pointer() in seen:
                continue
            seen.add(obj.as_pointer())
            original = obj.data
            imported = 'lol_skn_flags' in obj or BASIS in original
            if not imported:
                continue
            working = original.copy()
            copies.append((obj, original, working))
            prepare_export_mesh(working, imported=imported)
            obj.data = working
        bpy.context.view_layer.update()
        yield
    finally:
        for obj, original, working in reversed(copies):
            obj.data = original
            bpy.data.meshes.remove(working)
        if copies:
            bpy.context.view_layer.update()


def with_export_normals(function):
    @wraps(function)
    def wrapped(filepath, mesh_objects, *args, **kwargs):
        mesh_objects = list(mesh_objects)
        with export_meshes(mesh_objects):
            return function(filepath, mesh_objects, *args, **kwargs)
    return wrapped
