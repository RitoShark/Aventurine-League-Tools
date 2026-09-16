"""SKN mesh importer - fixed to match skeleton coordinate system and binding"""
import bpy
import os
import mathutils
import struct
from ..utils.binary_utils import Vector
from ..utils.backend import require_ritoshark
from . import import_skl
from ..utils import texture_manager


class SKNVertex:
    __slots__ = ('position', 'influences', 'weights', 'uv')
    
    def __init__(self):
        self.position = None
        self.influences = None
        self.weights = None
        self.uv = None


def read_skn(filepath):
    mesh = require_ritoshark().Skn.from_path(str(filepath))
    vertices = []
    for position, influences, weights, uv in zip(
        struct.iter_unpack('<3f', mesh.positions),
        struct.iter_unpack('<4I', mesh.blend_indices),
        struct.iter_unpack('<4f', mesh.blend_weights),
        struct.iter_unpack('<2f', mesh.uvs),
    ):
        vertex = SKNVertex()
        vertex.position = Vector(*position)
        vertex.influences = influences
        vertex.weights = weights
        vertex.uv = Vector(*uv)
        vertices.append(vertex)
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
    faces = [(indices[i], indices[i+1], indices[i+2]) for i in range(0, len(indices), 3)]
    mesh.from_pydata(verts, [], faces)
    
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for vert_idx, loop_idx in zip(poly.vertices, poly.loop_indices):
            vertex = vertices[vert_idx]
            uv_layer.data[loop_idx].uv = (vertex.uv.x, 1.0 - vertex.uv.y)
    
    for submesh in submeshes:
        mat = bpy.data.materials.new(submesh.name)
        mat.use_nodes = True
        mesh.materials.append(mat)
        
        face_start = submesh.index_start // 3
        face_end = (submesh.index_start + submesh.index_count) // 3
        mat_idx = len(mesh.materials) - 1
        for i in range(face_start, min(face_end, len(mesh.polygons))):
            mesh.polygons[i].material_index = mat_idx
    
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

        if auto_load_textures:
            try:
                texture_manager.import_textures(mesh_obj, filepath)
            except Exception as e:
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
        return {'FINISHED'}
    
    except Exception as e:
        operator.report({'ERROR'}, f'Failed: {str(e)}')
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
