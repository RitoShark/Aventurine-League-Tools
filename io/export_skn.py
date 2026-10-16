import bpy
import mathutils
import struct
import os
import re
import base64
import hashlib
from ..utils.format_data import write_skin
from . import import_skl
from . import bone_utils

def _corner_normals(mesh):
    if hasattr(mesh, 'calc_normals_split'):
        mesh.calc_normals_split()
    if hasattr(mesh, 'corner_normals'):
        return [normal.vector.copy() for normal in mesh.corner_normals]
    return [loop.normal.copy() for loop in mesh.loops]


def clean_blender_name(name):
    """Remove Blender's .001, .002 etc. suffixes from names"""
    return re.sub(r'\.\d{3}$', '', name)


def tangent_basis_signature(mesh):
    """Detect mesh edits that invalidate imported tangent attributes."""
    digest = hashlib.sha256()
    for vertex in mesh.vertices:
        digest.update(struct.pack('<3f', *vertex.co))
    normals = _corner_normals(mesh)
    uvs = mesh.uv_layers.active
    for loop in mesh.loops:
        digest.update(struct.pack('<I3f2f', loop.vertex_index, *normals[loop.index],
                                  *(uvs.data[loop.index].uv if uvs else (0, 0))))
    return digest.hexdigest()

def check_shared_vertices_between_materials(mesh_obj):
    """
    Check if any vertices are shared between faces with different materials.
    Returns a list of material names that share vertices, or empty list if none.
    """
    mesh = mesh_obj.data
    mesh.calc_loop_triangles()

    if len(mesh.materials) <= 1:
        return []

    # Map each vertex to the set of material indices that use it
    vertex_materials = {}
    for tri in mesh.loop_triangles:
        mat_idx = tri.material_index
        for v_idx in tri.vertices:
            if v_idx not in vertex_materials:
                vertex_materials[v_idx] = set()
            vertex_materials[v_idx].add(mat_idx)

    # Find vertices used by multiple materials
    shared_materials = set()
    for v_idx, mat_indices in vertex_materials.items():
        if len(mat_indices) > 1:
            for mat_idx in mat_indices:
                if mat_idx < len(mesh.materials) and mesh.materials[mat_idx]:
                    shared_materials.add(mesh.materials[mat_idx].name)

    return list(shared_materials)


def collect_mesh_data(mesh_obj, armature_obj, bone_to_idx, submesh_name, material_index=None, disable_scaling=False, disable_transforms=False, deformed_positions=None, deformed_loop_normals=None, apply_object_transform=True, model_scale=1.0, export_tangents=False):
    """
    Collect geometry data from a single mesh object.
    If material_index is specified, only collect triangles belonging to that material.

    IMPORTANT: In SKN format, vertices at UV seams must be duplicated (same position, different UV).
    This matches lol_maya's approach: for each vertex, create a separate SKN vertex for each unique UV.
    """

    mesh = mesh_obj.data
    mesh.calc_loop_triangles()
    # calc_normals_split was removed in Blender 4.1+
    if hasattr(mesh, 'calc_normals_split'):
        mesh.calc_normals_split()

    # Transform taking mesh-local coords into the space we export from.
    #   apply_object_transform: full WORLD space — keeps the armature object's
    #     rotation/scale/position, matching what's seen in Blender and what
    #     export_skl folds into the root bones (kept consistent between the two).
    #   off: ARMATURE-LOCAL (object transform dropped from both sides). When the
    #     armature is at identity these are identical, so this is the same result
    #     for every normal League skin.
    if apply_object_transform:
        mesh_matrix = mesh_obj.matrix_world.copy()
    else:
        mesh_matrix = armature_obj.matrix_world.inverted() @ mesh_obj.matrix_world
    world_to_armature = mesh_matrix
    # model_scale: uniform size multiplier, keep matched to the SKL and ANM Scale.
    scale = (1.0 if disable_scaling else import_skl.EXPORT_SCALE) * model_scale

    # Map vertex groups to SKL bone indices.
    # Exact name match takes priority — weights painted on "X.001" must reach
    # bone "X.001" when it exists, not leak onto native "X".  Cleaning strips
    # numeric suffixes only (.001), never at the first dot (Hand.L stays Hand.L).
    group_to_bone_idx = {}
    for group in mesh_obj.vertex_groups:
        if group.name in bone_to_idx:
            group_to_bone_idx[group.index] = bone_to_idx[group.name]
        else:
            clean_name = bone_utils.clean_export_bone_name(group.name)
            if clean_name in bone_to_idx:
                group_to_bone_idx[group.index] = bone_to_idx[clean_name]

    # Get UV data
    if not mesh.uv_layers.active:
        raise Exception(f"Mesh '{mesh_obj.name}' has no active UV layer")
    uv_data = mesh.uv_layers.active.data
    colors = mesh.color_attributes.active_color
    tangents = mesh.attributes.get('SKNTangent')
    tangent_signs = mesh.attributes.get('SKNTangentSign')
    if export_tangents and mesh_obj.get('lol_skn_tangent_basis') != tangent_basis_signature(mesh):
        tangents = tangent_signs = None
    if export_tangents and (tangents is None or tangent_signs is None or deformed_positions is not None):
        if deformed_positions is not None:
            raise ValueError('Apply the visual pose before exporting a mesh with SKN tangents')
        mesh.calc_tangents(uvmap=mesh.uv_layers.active.name)
        tangents = tangent_signs = None
    if export_tangents and tangents is not None and (tangents.domain != 'CORNER' or tangent_signs.domain != 'CORNER'):
        raise ValueError('SKN tangent attributes must use the face-corner domain')

    # Filter polygons by material index if specified
    if material_index is not None:
        filtered_polys = [poly for poly in mesh.polygons if poly.material_index == material_index]
    else:
        filtered_polys = list(mesh.polygons)

    if not filtered_polys:
        return None

    # Preserve authored corner normals, splitting vertices at both UV and normal seams.
    loop_normals = deformed_loop_normals if deformed_loop_normals is not None else _corner_normals(mesh)
    normal_matrix = world_to_armature.to_3x3().inverted_safe().transposed()
    if world_to_armature.to_3x3().determinant() < 0:
        normal_matrix = -normal_matrix

    unique_verts = {}
    submesh_vertices = []
    loop_to_vertex = {}

    # For each vertex, collect all unique UVs from loops that use it
    for poly in filtered_polys:
        for loop_idx in poly.loop_indices:
            v_idx = mesh.loops[loop_idx].vertex_index
            uv = uv_data[loop_idx].uv
            # Round UV to create stable key (avoid float comparison issues)
            uv_key = (round(uv[0], 6), round(uv[1], 6))

            normal = loop_normals[loop_idx]
            color = None
            if colors is not None:
                color_index = loop_idx if colors.domain == 'CORNER' else v_idx
                r, g, b, a = colors.data[color_index].color_srgb
                color = tuple(max(0, min(255, round(c * 255))) for c in (b, g, r, a))
            tangent = None
            if export_tangents:
                vector = tangents.data[loop_idx].vector if tangents else mesh.loops[loop_idx].tangent
                sign = tangent_signs.data[loop_idx].value if tangent_signs else mesh.loops[loop_idx].bitangent_sign
                vector = (world_to_armature.to_3x3() @ vector).normalized()
                tangent = ((*vector, -sign) if disable_transforms else (-vector.x, vector.z, -vector.y, -sign))
            vert_key = (v_idx, uv_key, tuple(round(c, 6) for c in normal), color, tangent)

            if vert_key not in unique_verts:
                v = mesh.vertices[v_idx]

                # Position - use deformed if available (for visual pose export)
                v_co = deformed_positions[v_idx] if deformed_positions else v.co
                v_B = world_to_armature @ v_co
                if disable_transforms:
                    v_L = mathutils.Vector((v_B.x * scale, v_B.y * scale, v_B.z * scale))
                else:
                    v_L = mathutils.Vector((-v_B.x * scale, v_B.z * scale, -v_B.y * scale))

                n_A = (normal_matrix @ normal).normalized()
                if disable_transforms:
                    n_L = mathutils.Vector((n_A.x, n_A.y, n_A.z))
                else:
                    # Normal transform for coordinate change with det = -1 (reflection):
                    # Position transform M: Blender(x,y,z) -> SKN(-x, z, -y)
                    # Normal transform = -M (cofactor): Blender(nx,ny,nz) -> SKN(nx, -nz, ny)
                    n_L = mathutils.Vector((n_A.x, -n_A.z, n_A.y))

                # Weights
                influences = [0, 0, 0, 0]
                weights = [0.0, 0.0, 0.0, 0.0]
                vg_weights = sorted([(group_to_bone_idx[g.group], g.weight)
                                   for g in v.groups if g.group in group_to_bone_idx],
                                  key=lambda x: x[1], reverse=True)

                for j in range(min(4, len(vg_weights))):
                    influences[j] = vg_weights[j][0]
                    weights[j] = vg_weights[j][1]

                w_sum = sum(weights)
                if w_sum > 0:
                    weights = [w / w_sum for w in weights]
                else:
                    weights = [1.0, 0.0, 0.0, 0.0]

                new_idx = len(submesh_vertices)
                unique_verts[vert_key] = new_idx
                submesh_vertices.append({
                    'pos': v_L,
                    'inf': influences,
                    'weight': weights,
                    'normal': n_L,
                    'uv': (uv[0], 1.0 - uv[1])
                })
                if color is not None:
                    submesh_vertices[-1]['color'] = color
                if tangent is not None:
                    submesh_vertices[-1]['tangent'] = tangent
            loop_to_vertex[loop_idx] = unique_verts[vert_key]

    # Use Blender's triangulation, including concave polygons.
    submesh_indices = []
    for triangle in mesh.loop_triangles:
        if material_index is None or triangle.material_index == material_index:
            submesh_indices.extend(loop_to_vertex[i] for i in triangle.loops)

    return {
        'name': submesh_name,
        'vertices': submesh_vertices,
        'indices': submesh_indices
    }


def write_skn_multi(filepath, mesh_objects, armature_obj, clean_names=True, disable_scaling=False, disable_transforms=False, use_visual_pose=False, apply_object_transform=True, model_scale=1.0, influences=None):
    """Write multiple Blender meshes to a single SKN file with multiple submeshes"""

    if not armature_obj:
        raise Exception("No armature found")

    from .skeleton import ExportPlan, has_offset_parent
    plan = ExportPlan(armature_obj, clean_names)
    plan.validate_offset_bind()
    bone_list = plan.bones
    use_visual_pose = use_visual_pose or has_offset_parent(armature_obj)
    # Build bone name to index map, with cleaned names if option enabled
    bone_to_idx = {}
    for i, bone in enumerate(bone_list):
        bone_to_idx[bone.name] = i
    if clean_names:
        for i, bone in enumerate(bone_list):
            # Also map cleaned name to same index for vertex group lookup.
            # Never overwrite an exact bone name: the alias of "X.001" must not
            # hijack groups that target the real bone "X".
            cleaned = bone_utils.clean_export_bone_name(bone.name)
            if cleaned != bone.name and cleaned not in bone_to_idx:
                bone_to_idx[cleaned] = i

    # If use_visual_pose, evaluate meshes at frame 0 with armature deformation
    # This gives us vertex positions that match the new bind pose from the SKL
    deformed_data = {}
    if use_visual_pose:
        current_frame = bpy.context.scene.frame_current
        bpy.context.scene.frame_set(0)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for obj in mesh_objects:
            if obj.type != 'MESH':
                continue
            eval_obj = obj.evaluated_get(depsgraph)
            eval_mesh = eval_obj.to_mesh()
            if (len(eval_mesh.vertices) != len(obj.data.vertices)
                    or [loop.vertex_index for loop in eval_mesh.loops]
                    != [loop.vertex_index for loop in obj.data.loops]):
                eval_obj.to_mesh_clear()
                bpy.context.scene.frame_set(current_frame)
                raise ValueError(f"Mesh '{obj.name}' has topology-changing modifiers; apply them before visual-pose export")
            positions = {vi: eval_mesh.vertices[vi].co.copy() for vi in range(len(eval_mesh.vertices))}
            loop_normals = _corner_normals(eval_mesh)
            deformed_data[obj.name] = (positions, loop_normals)
            eval_obj.to_mesh_clear()
        bpy.context.scene.frame_set(current_frame)

    source_metadata = {(int(obj.get('lol_skn_flags', 0)), obj.get('lol_skn_prefix', ''),
                        obj.get('lol_skn_trailing', base64.b64encode(bytes(12)).decode('ascii')))
                       for obj in mesh_objects if obj.type == 'MESH' and 'lol_skn_flags' in obj}
    if len(source_metadata) > 1:
        raise ValueError('Selected meshes have incompatible SKN file metadata; export them separately')
    flags, prefix, trailing = next(iter(source_metadata), (0, '', base64.b64encode(bytes(12)).decode('ascii')))
    export_tangents = any(obj.get('lol_skn_has_tangents', False) for obj in mesh_objects)
    submesh_data = []
    total_vertex_count = 0
    total_index_count = 0

    for mesh_obj in mesh_objects:
        if mesh_obj.type != 'MESH':
            continue

        mesh = mesh_obj.data
        deformed_pos, deformed_norms = deformed_data.get(mesh_obj.name, (None, None))

        # Check for shared vertices between materials
        shared_mats = check_shared_vertices_between_materials(mesh_obj)
        if shared_mats:
            raise Exception(
                f"Mesh '{mesh_obj.name}' has vertices shared between multiple materials: {', '.join(shared_mats)}. "
                f"Please separate the mesh by material (Edit Mode > Mesh > Separate > By Material) before exporting."
            )

        # Process each material on the mesh as a separate submesh
        if mesh.materials:
            for mat_idx, material in enumerate(mesh.materials):
                if material is None:
                    submesh_name = mesh_obj.name
                else:
                    submesh_name = material.get('lol_material_name', material.name)

                # Clean up Maya-style "mesh_" prefix
                if submesh_name.startswith("mesh_"):
                    submesh_name = submesh_name[5:]

                if clean_names and (material is None or 'lol_material_name' not in material):
                    submesh_name = clean_blender_name(submesh_name)

                data = collect_mesh_data(mesh_obj, armature_obj, bone_to_idx, submesh_name,
                                        material_index=mat_idx,
                                        disable_scaling=disable_scaling,
                                        disable_transforms=disable_transforms,
                                        deformed_positions=deformed_pos,
                                        deformed_loop_normals=deformed_norms,
                                        apply_object_transform=apply_object_transform,
                                        model_scale=model_scale, export_tangents=export_tangents)

                if data is None or not data['indices']:
                    continue

                submesh_info = {
                    'name': data['name'],
                    'vertex_start': total_vertex_count,
                    'vertex_count': len(data['vertices']),
                    'index_start': total_index_count,
                    'index_count': len(data['indices']),
                    'vertices': data['vertices'],
                    'indices': [idx + total_vertex_count for idx in data['indices']]
                }

                submesh_data.append(submesh_info)
                total_vertex_count += len(data['vertices'])
                total_index_count += len(data['indices'])
        else:
            # No materials - use mesh object name
            submesh_name = mesh_obj.name
            if submesh_name.startswith("mesh_"):
                submesh_name = submesh_name[5:]
            if clean_names:
                submesh_name = clean_blender_name(submesh_name)

            data = collect_mesh_data(mesh_obj, armature_obj, bone_to_idx, submesh_name,
                                    material_index=None,
                                    disable_scaling=disable_scaling,
                                    disable_transforms=disable_transforms,
                                    deformed_positions=deformed_pos,
                                    deformed_loop_normals=deformed_norms,
                                    apply_object_transform=apply_object_transform,
                                    model_scale=model_scale, export_tangents=export_tangents)

            if data is None or not data['indices']:
                continue

            submesh_info = {
                'name': data['name'],
                'vertex_start': total_vertex_count,
                'vertex_count': len(data['vertices']),
                'index_start': total_index_count,
                'index_count': len(data['indices']),
                'vertices': data['vertices'],
                'indices': [idx + total_vertex_count for idx in data['indices']]
            }

            submesh_data.append(submesh_info)
            total_vertex_count += len(data['vertices'])
            total_index_count += len(data['indices'])

    if not submesh_data:
        raise Exception("No geometry found to export")
    
    # Validate limits (same as Maya plugin)
    if total_vertex_count > 65535:
        raise Exception(f"Too many vertices: {total_vertex_count}, max allowed: 65535. Reduce mesh complexity or split into multiple files.")
    
    if len(submesh_data) > 32:
        raise Exception(f"Too many submeshes/materials: {len(submesh_data)}, max allowed: 32. Reduce number of materials.")

    # SKN vertex influence bytes are indices into a per-mesh bone PALETTE (the
    # SKL "influences" table), NOT raw skeleton bone indices.  The engine reads
    # influences[byte] -> real joint index, so the skeleton may hold up to 32768
    # bones while a single SKN weights up to 256 distinct ones.  Writing the raw
    # joint index (as the old code did) overflows the uint8 the moment any
    # weighted bone sits past index 255 — even if the mesh only touches a few
    # hundred bones out of a larger rig.  Build the palette from the bones the
    # mesh actually uses, then remap every vertex to compact palette indices.
    #
    # Only slots carrying weight define a used bone.  Padding slots of verts with
    # <4 influences hold (bone 0, weight 0); they must not inflate the palette
    # and are remapped to a harmless in-range 0 below.
    used_joints = set()
    for sm in submesh_data:
        for v in sm['vertices']:
            for bone_idx, w in zip(v['inf'], v['weight']):
                if w > 0:
                    used_joints.add(bone_idx)

    if influences is None:
        influences = sorted(used_joints)  # paired export builds a compact palette
    else:
        influences = list(influences)  # SKN-only export must retain existing slots
        if not used_joints.issubset(influences):
            raise ValueError('Mesh weights reference bones outside the existing skinning palette. Export SKN and SKL together.')
    if len(influences) > 256:
        raise Exception(
            f"SKN format supports at most 256 bones influencing one mesh, but its "
            f"vertices are weighted to {len(influences)} distinct bones. Reduce the "
            f"number of bones the mesh is weighted to (only weighted bones count — "
            f"the full skeleton may still exceed this)."
        )

    joint_to_palette = {joint_idx: pi for pi, joint_idx in enumerate(influences)}
    # Remap each vertex's influence bytes from real joint index -> palette index.
    for sm in submesh_data:
        for v in sm['vertices']:
            v['inf'] = [joint_to_palette[b] if w > 0 else 0
                        for b, w in zip(v['inf'], v['weight'])]

    write_skin(filepath, submesh_data, flags=flags, prefix_block=base64.b64decode(prefix, validate=True),
               trailing=base64.b64decode(trailing, validate=True))

    return len(submesh_data), total_vertex_count, influences


def fix_custom_bone_parenting(armature_obj):
    """Compatibility shim: export never mutates a user's bone hierarchy."""
    return []


def process_animations_visual(operator, context, skn_filepath, armature_obj,
                              disable_scaling=False, disable_transforms=False,
                              clean_names=True, anim_scale=1.0, apply_object_transform=True):
    """Rebake clips through the same importer/export plan as interactive export."""
    import shutil
    import tempfile
    from pathlib import Path
    from . import import_anm, export_anm
    directory = Path(skn_filepath).resolve().parent
    animations = directory / 'animations'
    backup = directory / 'Unmodified_anm_backup'
    if not animations.is_dir():
        operator.report({'WARNING'}, f'No animations folder found at {animations}')
        return False
    if not backup.exists():
        backup.mkdir()
        for path in animations.glob('*.anm'):
            shutil.copy2(path, backup / path.name)
    paths = sorted(backup.glob('*.anm'))
    if not paths:
        operator.report({'WARNING'}, 'No animations to process')
        return False
    armature_obj.animation_data_create()
    original = armature_obj.animation_data.action
    frame = context.scene.frame_current
    frame_range = (context.scene.frame_start, context.scene.frame_end)
    pose = {pb.name: pb.matrix_basis.copy() for pb in armature_obj.pose.bones}
    try:
        with tempfile.TemporaryDirectory(prefix='.aventurine-anm-', dir=animations) as staging:
            for path in paths:
                action = bpy.data.actions.new('__aventurine_rebake')
                armature_obj.animation_data.action = action
                try:
                    clip = import_anm.read_anm(path)
                    import_anm.apply_anm(clip, armature_obj)
                    export_anm.write_anm(Path(staging) / path.name, armature_obj, clip.fps,
                        disable_scaling=disable_scaling, disable_transforms=disable_transforms,
                        visual_mode=True, clean_names=clean_names, anim_scale=anim_scale,
                        apply_object_transform=apply_object_transform)
                finally:
                    armature_obj.animation_data.action = None
                    bpy.data.actions.remove(action)
            # Do not replace any clip if a conversion failed.
            for path in paths:
                os.replace(Path(staging) / path.name, animations / path.name)
    finally:
        armature_obj.animation_data.action = original
        for pb in armature_obj.pose.bones:
            pb.matrix_basis = pose[pb.name]
        context.scene.frame_start, context.scene.frame_end = frame_range
        context.scene.frame_set(frame)
    operator.report({'INFO'}, f'Processed {len(paths)} animations')
    return True


def save(operator, context, filepath, export_skl_file=True, clean_names=True, target_armature=None, disable_scaling=False, disable_transforms=False, use_visual_pose=False, apply_object_transform=True, model_scale=1.0):
    armature_obj = target_armature
    mesh_objects = []
    
    # Get all selected meshes
    selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']
    
    if selected_meshes:
        mesh_objects = selected_meshes
        
        if not armature_obj:
            armature_obj = selected_meshes[0].find_armature()
            if not armature_obj and selected_meshes[0].parent and selected_meshes[0].parent.type == 'ARMATURE':
                armature_obj = selected_meshes[0].parent
                
    elif armature_obj:
        mesh_objects = [obj for obj in context.scene.objects 
                       if obj.type == 'MESH' and 
                       (obj.parent == armature_obj or obj.find_armature() == armature_obj)]
                       
    elif context.active_object and context.active_object.type == 'ARMATURE':
        armature_obj = context.active_object
        mesh_objects = [obj for obj in context.scene.objects 
                       if obj.type == 'MESH' and 
                       (obj.parent == armature_obj or obj.find_armature() == armature_obj)]
    else:
        armature_obj = next((obj for obj in context.scene.objects if obj.type == 'ARMATURE'), None)
        if armature_obj:
            mesh_objects = [obj for obj in context.scene.objects 
                           if obj.type == 'MESH' and 
                           (obj.parent == armature_obj or obj.find_armature() == armature_obj)]
    
    if not mesh_objects:
        operator.report({'ERROR'}, "No mesh objects found. Select meshes or select the armature to export all.")
        return {'CANCELLED'}
    
    if not armature_obj:
        operator.report({'ERROR'}, "No armature found. Meshes must be parented to an armature.")
        return {'CANCELLED'}
    
    try:
        from .skeleton import ExportPlan
        palette = None if export_skl_file else ExportPlan(armature_obj, clean_names).skin_palette()
        submesh_count, vertex_count, skn_influences = write_skn_multi(filepath, mesh_objects, armature_obj, clean_names, disable_scaling, disable_transforms, use_visual_pose, apply_object_transform=apply_object_transform, model_scale=model_scale, influences=palette)
        operator.report({'INFO'}, f"Exported SKN: {submesh_count} submeshes, {vertex_count} vertices, {len(skn_influences)} bone influences")

        if export_skl_file and armature_obj:
            skl_path = os.path.splitext(filepath)[0] + ".skl"
            from . import export_skl
            export_skl.write_skl(skl_path, armature_obj, disable_scaling, disable_transforms, use_visual_pose,
                                 clean_names=clean_names, apply_object_transform=apply_object_transform, model_scale=model_scale,
                                 influences=skn_influences)
            operator.report({'INFO'}, f"Exported matching SKL: {skl_path}")
            
        if any(pb.get('lol_offset_companion_of') for pb in armature_obj.pose.bones):
            operator.report({'WARNING'}, 'Linked root offsets require updated ANMs. Export animations or use batch conversion; unchanged Riot clips will not move the linked roots.')
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
