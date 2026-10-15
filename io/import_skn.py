"""SKN mesh importer - fixed to match skeleton coordinate system and binding"""
import bpy
import os
import mathutils
import struct
import base64
from ..utils.binary_utils import Vector
from ..utils.backend import require_ritoshark
from . import import_skl
from ..utils import texture_manager


class SKNVertex:
    __slots__ = ('position', 'normal', 'influences', 'weights', 'uv', 'color', 'tangent')
    
    def __init__(self):
        self.position = None
        self.normal = None
        self.influences = None
        self.weights = None
        self.uv = None
        self.color = None
        self.tangent = None


class SKNVertices(list):
    """Vertices plus file metadata, retained through mesh creation."""


def read_skn(filepath):
    mesh = require_ritoshark().Skn.from_path(str(filepath))
    vertices = SKNVertices()
    vertices.flags = mesh.flags
    vertices.prefix_block = mesh.prefix_block
    vertices.trailing = mesh.trailing
    for position, normal, influences, weights, uv in zip(
        struct.iter_unpack('<3f', mesh.positions),
        struct.iter_unpack('<3f', mesh.normals),
        struct.iter_unpack('<4I', mesh.blend_indices),
        struct.iter_unpack('<4f', mesh.blend_weights),
        struct.iter_unpack('<2f', mesh.uvs),
    ):
        vertex = SKNVertex()
        vertex.position = Vector(*position)
        vertex.normal = Vector(*normal)
        vertex.influences = influences
        vertex.weights = weights
        vertex.uv = Vector(*uv)
        vertices.append(vertex)
    if mesh.colors is not None:
        for vertex, (b, g, r, a) in zip(vertices, struct.iter_unpack('4B', mesh.colors)):
            vertex.color = (r / 255, g / 255, b / 255, a / 255)
    if mesh.tangents is not None:
        for vertex, tangent in zip(vertices, struct.iter_unpack('<4f', mesh.tangents)):
            vertex.tangent = tangent
    indices = [item[0] for item in struct.iter_unpack('<I', mesh.indices)]
    submeshes = mesh.submeshes
    if not submeshes:
        submeshes = [require_ritoshark().Submesh('Base', 0, mesh.vertex_count, 0, len(indices))]
    return indices, vertices, submeshes


def create_mesh(indices, vertices, submeshes, name, armature_obj=None, joints=None, influences=None):
    # Coordinate system: X -> X, Y -> Z, Z -> -Y (Standing Up)
    # Apply import scale (0.01) to match Maya/LtMAO units
    scale = import_skl.IMPORT_SCALE
    verts = []
    for v in vertices:
        # Flip X to match Maya/Reliable import (Sword in left hand)
        verts.append((-v.position.x * scale, -v.position.z * scale, v.position.y * scale))
    
    mesh = bpy.data.meshes.new(name)
    if len(indices) % 3 or any(i < 0 or i >= len(vertices) for i in indices):
        raise ValueError('SKN contains invalid triangle indices')
    # Discard zero-area index triples without shifting material assignments.
    face_sources = [i for i in range(0, len(indices), 3) if len(set(indices[i:i+3])) == 3]
    faces = [tuple(indices[i:i+3]) for i in face_sources]
    mesh.from_pydata(verts, [], faces)

    if all(v.normal is not None for v in vertices):
        # Positions are reflected while triangle winding is retained. Transform
        # normals with the cofactor (-P), matching the export convention.
        normals = [(v.normal.x, v.normal.z, -v.normal.y) for v in vertices]
        for polygon in mesh.polygons:
            polygon.use_smooth = True
        if hasattr(mesh, 'use_auto_smooth'):
            mesh.use_auto_smooth = True
        mesh.normals_split_custom_set_from_vertices(normals)
    
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj['lol_skn_flags'] = getattr(vertices, 'flags', 0)
    obj['lol_skn_prefix'] = base64.b64encode(getattr(vertices, 'prefix_block', b'')).decode('ascii')
    obj['lol_skn_trailing'] = base64.b64encode(getattr(vertices, 'trailing', bytes(12))).decode('ascii')
    if vertices and all(v.color is not None for v in vertices):
        colors = mesh.color_attributes.new(name='SKNColor', type='BYTE_COLOR', domain='CORNER')
        for loop in mesh.loops:
            colors.data[loop.index].color_srgb = vertices[loop.vertex_index].color
    if vertices and all(v.tangent is not None for v in vertices):
        tangent = mesh.attributes.new(name='SKNTangent', type='FLOAT_VECTOR', domain='CORNER')
        sign = mesh.attributes.new(name='SKNTangentSign', type='FLOAT', domain='CORNER')
        # Creating attributes may invalidate earlier RNA references.
        tangent = mesh.attributes['SKNTangent']
        for loop in mesh.loops:
            x, y, z, w = vertices[loop.vertex_index].tangent
            tangent.data[loop.index].vector = (-x, -z, y)
            sign.data[loop.index].value = -w
        obj['lol_skn_has_tangents'] = True
    
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for vert_idx, loop_idx in zip(poly.vertices, poly.loop_indices):
            vertex = vertices[vert_idx]
            uv_layer.data[loop_idx].uv = (vertex.uv.x, 1.0 - vertex.uv.y)
    
    for submesh in submeshes:
        mat = bpy.data.materials.new(submesh.name)
        mat.use_nodes = True
        mat['lol_material_name'] = submesh.name
        mesh.materials.append(mat)
        
        face_start = submesh.index_start // 3
        face_end = (submesh.index_start + submesh.index_count) // 3
        mat_idx = len(mesh.materials) - 1
        for polygon, source in zip(mesh.polygons, face_sources):
            if face_start <= source // 3 < face_end:
                polygon.material_index = mat_idx
    
    # Bind to armature
    if armature_obj and joints:
        obj.parent = armature_obj
        obj.matrix_parent_inverse = armature_obj.matrix_world.inverted()
        
        group_map = {}
        for i, joint in enumerate(joints):
            vg = obj.vertex_groups.new(name=joint.name)
            group_map[i] = vg
        
        mod = obj.modifiers.new(name='Armature', type='ARMATURE')
        mod.object = armature_obj
        mod.use_vertex_groups = True
        mod.use_bone_envelopes = False
        
        for vtx_idx, vertex in enumerate(vertices):
            # Safe way to handle multi-influence with possibly mapped indices
            v_weights = {}
            for k in range(4):
                bone_id = vertex.influences[k]
                weight = vertex.weights[k]
                
                if weight > 0.0001:
                    actual_joint_idx = bone_id
                    if influences and bone_id < len(influences):
                        actual_joint_idx = influences[bone_id]
                    
                    if actual_joint_idx < len(joints):
                        v_weights[actual_joint_idx] = v_weights.get(actual_joint_idx, 0.0) + weight
            
            # Add to groups
            for joint_idx, combined_weight in v_weights.items():
                group_map[joint_idx].add([vtx_idx], combined_weight, 'ADD')
    
    if obj.get('lol_skn_has_tangents'):
        from .export_skn import tangent_basis_signature
        obj['lol_skn_tangent_basis'] = tangent_basis_signature(mesh)
    return obj


def load(operator, context, filepath, load_skl_file=True, split_by_material=False, auto_load_textures=True, bone_orient='VISUAL'):
    try:
        indices, vertices, submeshes = read_skn(filepath)
        
        armature_obj = None
        joints = None
        influences = None
        
        if load_skl_file:
            skl_path = filepath.lower().replace('.skn', '.skl')
            if not os.path.exists(skl_path):
                skl_path = filepath.replace('.skn', '.skl')
                
            if os.path.exists(skl_path):
                try:
                    joints, influences = import_skl.read_skl(skl_path)
                    armature_obj = import_skl.create_armature(joints, bone_orient=bone_orient)
                except Exception as e:
                    operator.report({'WARNING'}, f'SKL load failed: {str(e)}')
        
        name = os.path.splitext(os.path.basename(filepath))[0]
        mesh_obj = create_mesh(indices, vertices, submeshes, name, armature_obj, joints, influences)

        texture_issues = []
        if auto_load_textures:
            try:
                texture_issues = texture_manager.import_textures(mesh_obj, filepath)
            except Exception as e:
                texture_issues.append(f"Texture loading failed: {e}")
                print(f"Texture import warnings: {e}")
                import traceback
                traceback.print_exc()

        
        if split_by_material:
            bpy.ops.object.select_all(action='DESELECT')
            mesh_obj.select_set(True)
            context.view_layer.objects.active = mesh_obj
            bpy.ops.mesh.separate(type='MATERIAL')
            
            for obj in context.selected_objects:
                if obj.type == 'MESH' and len(obj.data.materials) > 0:
                    mat = obj.data.materials[0]
                    if mat:
                        obj.name = mat.name
                    obj["lol_skn_filepath"] = filepath
                    obj["lol_skn_filename"] = os.path.basename(filepath)
        else:
            mesh_obj["lol_skn_filepath"] = filepath
            mesh_obj["lol_skn_filename"] = os.path.basename(filepath)
        
        if armature_obj:
            armature_obj["lol_skn_filepath"] = filepath
            armature_obj["lol_skl_filepath"] = filepath.replace('.skn', '.skl')
            
            # Automatically save the import pose as the bind pose
            import json
            bind_pose_data = {}
            for pose_bone in armature_obj.pose.bones:
                # Store matrix_basis (local transformation) as a flat list (4x4 = 16 values)
                # This is the local transformation relative to the parent bone
                matrix = pose_bone.matrix_basis
                matrix_list = []
                for row in matrix:
                    matrix_list.extend(row)
                bind_pose_data[pose_bone.name] = matrix_list
            
            # Save to custom property as JSON
            armature_obj["lol_bind_pose"] = json.dumps(bind_pose_data)
        
        operator.report({'INFO'}, f'Imported {len(vertices)} vertices')
        texture_manager.report_texture_issues(operator, texture_issues)
        return {'FINISHED'}
    
    except Exception as e:
        operator.report({'ERROR'}, f'Failed: {str(e)}')
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
