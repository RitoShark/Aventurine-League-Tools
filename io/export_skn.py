import bpy
import mathutils
import struct
import os
import re
from ..utils.binary_utils import BinaryStream
from . import import_skl
from . import bone_utils

def clean_blender_name(name):
    """Remove Blender's .001, .002 etc. suffixes from names"""
    return re.sub(r'\.\d{3}$', '', name)

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


def collect_mesh_data(mesh_obj, armature_obj, bone_to_idx, submesh_name, material_index=None, disable_scaling=False, disable_transforms=False, deformed_positions=None, deformed_poly_normals=None, apply_object_transform=True, model_scale=1.0):
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

    # Filter polygons by material index if specified
    if material_index is not None:
        filtered_polys = [poly for poly in mesh.polygons if poly.material_index == material_index]
    else:
        filtered_polys = list(mesh.polygons)

    if not filtered_polys:
        return None

    # === PHASE 0: Pre-calculate normals per mesh vertex (like Maya does) ===
    # Calculate averaged normals for each mesh vertex before UV splitting
    vertex_normals = {}
    for poly in filtered_polys:
        for v_idx in poly.vertices:
            if v_idx not in vertex_normals:
                vertex_normals[v_idx] = []
            # Use deformed face normal if available (for visual pose export)
            normal = deformed_poly_normals.get(poly.index, poly.normal) if deformed_poly_normals else poly.normal
            vertex_normals[v_idx].append(normal)
    
    # Average the normals for each vertex
    averaged_normals = {}
    for v_idx, normals_list in vertex_normals.items():
        avg = mathutils.Vector((0, 0, 0))
        for n in normals_list:
            avg += n
        avg.normalize()
        averaged_normals[v_idx] = avg

    # === PHASE 1: Build unique vertices based on (vertex_index, UV) pairs ===
    # This is the key fix: a vertex with multiple UVs (at UV seams) becomes multiple SKN vertices
    # Map: (v_idx, uv_key) -> new_vertex_index
    unique_verts = {}
    submesh_vertices = []

    # For each vertex, collect all unique UVs from loops that use it
    for poly in filtered_polys:
        for loop_idx in poly.loop_indices:
            v_idx = mesh.loops[loop_idx].vertex_index
            uv = uv_data[loop_idx].uv
            # Round UV to create stable key (avoid float comparison issues)
            uv_key = (round(uv[0], 6), round(uv[1], 6))

            vert_key = (v_idx, uv_key)

            if vert_key not in unique_verts:
                v = mesh.vertices[v_idx]

                # Position - use deformed if available (for visual pose export)
                v_co = deformed_positions[v_idx] if deformed_positions else v.co
                v_B = world_to_armature @ v_co
                if disable_transforms:
                    v_L = mathutils.Vector((v_B.x * scale, v_B.y * scale, v_B.z * scale))
                else:
                    v_L = mathutils.Vector((-v_B.x * scale, v_B.z * scale, -v_B.y * scale))

                # Normal - use pre-calculated averaged normal for this mesh vertex
                if v_idx in averaged_normals:
                    n_B = averaged_normals[v_idx]
                else:
                    # Fallback
                    n_B = mesh.loops[loop_idx].normal
                
                n_A = (world_to_armature.to_3x3() @ n_B).normalized()
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

    # === PHASE 2: Build triangle indices using the unique vertex mapping ===
    # For each triangle, look up the correct SKN vertex using (vertex_index, UV) key
    submesh_indices = []

    for poly in filtered_polys:
        loop_indices = list(poly.loop_indices)
        # Triangulate polygon using fan method
        for i in range(1, len(loop_indices) - 1):
            tri_loops = [loop_indices[0], loop_indices[i], loop_indices[i + 1]]
            for loop_idx in tri_loops:
                v_idx = mesh.loops[loop_idx].vertex_index
                uv = uv_data[loop_idx].uv
                uv_key = (round(uv[0], 6), round(uv[1], 6))
                vert_key = (v_idx, uv_key)
                submesh_indices.append(unique_verts[vert_key])

    return {
        'name': submesh_name,
        'vertices': submesh_vertices,
        'indices': submesh_indices
    }


def write_skn_multi(filepath, mesh_objects, armature_obj, clean_names=True, disable_scaling=False, disable_transforms=False, use_visual_pose=False, apply_object_transform=True, model_scale=1.0):
    """Write multiple Blender meshes to a single SKN file with multiple submeshes"""

    if not armature_obj:
        raise Exception("No armature found")

    # Sort bones by original import order (native bones first, new bones appended)
    # This MUST match export_skl.py ordering for consistent influence indices
    native_bones = []
    new_bones = []
    for b in armature_obj.pose.bones:
        idx = b.get("native_bone_index")
        if idx is not None:
            native_bones.append((int(idx), b))
        else:
            new_bones.append(b)
    native_bones.sort(key=lambda x: x[0])
    bone_list = [b for _, b in native_bones] + new_bones
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
            positions = {vi: eval_mesh.vertices[vi].co.copy() for vi in range(len(eval_mesh.vertices))}
            poly_normals = {p.index: p.normal.copy() for p in eval_mesh.polygons}
            deformed_data[obj.name] = (positions, poly_normals)
            eval_obj.to_mesh_clear()
        bpy.context.scene.frame_set(current_frame)

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
                    submesh_name = material.name

                # Clean up Maya-style "mesh_" prefix
                if submesh_name.startswith("mesh_"):
                    submesh_name = submesh_name[5:]

                if clean_names:
                    submesh_name = clean_blender_name(submesh_name)

                data = collect_mesh_data(mesh_obj, armature_obj, bone_to_idx, submesh_name,
                                        material_index=mat_idx,
                                        disable_scaling=disable_scaling,
                                        disable_transforms=disable_transforms,
                                        deformed_positions=deformed_pos,
                                        deformed_poly_normals=deformed_norms,
                                        apply_object_transform=apply_object_transform,
                                        model_scale=model_scale)

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
                                    deformed_poly_normals=deformed_norms,
                                    apply_object_transform=apply_object_transform,
                                    model_scale=model_scale)

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
    # influences[byte] -> real joint index, so the skeleton may hold up to 65535
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

    influences = sorted(used_joints)  # palette index -> real joint index
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

    # Write to file
    with open(filepath, 'wb') as f:
        bs = BinaryStream(f)
        
        bs.write_uint32(0x00112233)  # Magic
        bs.write_uint16(1, 1)  # Major, Minor
        
        bs.write_uint32(len(submesh_data))
        for sm in submesh_data:
            bs.write_padded_string(sm['name'], 64)
            bs.write_uint32(sm['vertex_start'], sm['vertex_count'], 
                           sm['index_start'], sm['index_count'])
            
        bs.write_uint32(total_index_count, total_vertex_count)
        
        for sm in submesh_data:
            for idx in sm['indices']:
                bs.write_uint16(idx)
                
        for sm in submesh_data:
            for v in sm['vertices']:
                bs.write_vec3(v['pos'])
                bs.write_uint8(*v['inf'])
                bs.write_float(*v['weight'])
                bs.write_vec3(v['normal'])
                bs.write_vec2(v['uv'])

    return len(submesh_data), total_vertex_count, influences


def fix_custom_bone_parenting(armature_obj):
    """
    Auto-repair Edit Mode parenting for custom bones.
    Returns list of (bone_name, new_parent_name) pairs for every change applied.
    """
    import re

    reparent_ops = []
    intended_parent = {}

    def current_or_intended_parent(name):
        if name in intended_parent:
            return intended_parent[name]
        pb = armature_obj.pose.bones.get(name)
        return pb.parent.name if (pb and pb.parent) else ""

    # Option C: Shift+D duplicates of native bones.  Blender copies the
    # pose-bone custom props on duplication, so these SHARE native_bone_index
    # with the original and are invisible to Options A/B below (which only
    # process prop-less bones).  Splice each duplicate between the original
    # bone and its current parent: the duplicate becomes an "offset holder"
    # whose authored placement shifts the subtree in every stock animation
    # (export_skl writes its bind as the authored delta, export_anm keeps it
    # trackless).
    offset_clones = bone_utils.detect_offset_clones(armature_obj)
    spliced_targets = set()
    for clone_name in sorted(offset_clones):
        target_name = offset_clones[clone_name]
        clone_pb = armature_obj.pose.bones.get(clone_name)
        target_pb = armature_obj.pose.bones.get(target_name)
        if clone_pb is None or target_pb is None:
            continue
        if clone_pb.get("native_bone_index") is None:
            continue  # prop-less name-matched duplicates are handled by Option B
        if target_name in spliced_targets:
            continue  # extra duplicates of the same bone are left as-is
        spliced_targets.add(target_name)
        target_parent = current_or_intended_parent(target_name)
        if target_parent == clone_name:
            continue  # already spliced
        clone_parent = clone_pb.parent.name if clone_pb.parent else ""
        if clone_parent != target_parent:
            reparent_ops.append((clone_name, target_parent))
            intended_parent[clone_name] = target_parent
        reparent_ops.append((target_name, clone_name))
        intended_parent[target_name] = clone_name

    for pb in armature_obj.pose.bones:
        idx = pb.get("native_bone_index")
        if idx is not None:
            continue  # native bone

        # Option A: custom bone already parents some native bones
        native_children = [c for c in pb.children
                           if c.get("native_bone_index") is not None]
        if native_children:
            np_names = [c.get("native_parent", "") for c in native_children
                        if c.get("native_parent")]
            if not np_names:
                continue
            correct_parent = np_names[0]
            if correct_parent not in armature_obj.pose.bones:
                continue
            current_parent = pb.parent.name if pb.parent else ""
            if current_parent != correct_parent:
                reparent_ops.append((pb.name, correct_parent))
                intended_parent[pb.name] = correct_parent
            continue

        # Option B: floating custom bone — use name suffix
        base_name = re.sub(r'\.\d+$', '', pb.name)
        if base_name == pb.name:
            continue

        native_match = armature_obj.pose.bones.get(base_name)
        if native_match is None or native_match.get("native_bone_index") is None:
            continue

        native_current_parent = current_or_intended_parent(native_match.name)

        if native_current_parent != pb.name:
            current_pb_parent = pb.parent.name if pb.parent else ""
            if current_pb_parent != native_current_parent:
                if not native_current_parent or native_current_parent in armature_obj.pose.bones:
                    reparent_ops.append((pb.name, native_current_parent))
                    intended_parent[pb.name] = native_current_parent

        actual_native_parent = native_match.parent.name if native_match.parent else ""
        if actual_native_parent != pb.name:
            reparent_ops.append((native_match.name, pb.name))
            intended_parent[native_match.name] = pb.name

    if not reparent_ops:
        return reparent_ops

    # Apply in Edit Mode
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = armature_obj

    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.mode_set(mode='EDIT')

    eb = armature_obj.data.edit_bones
    for bone_name, parent_name in reparent_ops:
        if bone_name not in eb:
            print(f"  WARNING '{bone_name}' not in edit_bones")
            continue
        if parent_name and parent_name in eb:
            eb[bone_name].parent = eb[parent_name]
        else:
            eb[bone_name].parent = None
        eb[bone_name].use_connect = False

    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = prev_active

    return reparent_ops


def process_animations_visual(operator, context, skn_filepath, armature_obj, disable_scaling=False, disable_transforms=False):
    """Re-export every .anm in the 'animations' folder next to the SKN so the
    animation transforms match a visually-exported skeleton.

    Approach: directly modify native ANM track values in League space — no
    Blender FK chain involved.  For each native bone whose Blender parent is a
    custom intermediate bone (e.g. R_Clavicle parented under R_Clavicle.001):

        l_visual[frame] = l_intermediate_bind_inv @ l_native[frame]

    This ensures the game reconstructs the original world position:
        parent_world × l_intermediate_bind × l_visual = parent_world × l_native

    All other bone tracks are written unchanged.  Custom intermediate bones are
    not added to the visual ANM — the game uses their SKL bind pose, which is
    correct (they should stay at rest).

    Returns True on (partial) success, False only when the animations folder is absent.
    """
    import shutil
    from . import import_anm, export_anm

    P     = mathutils.Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    P_inv = P.inverted()

    skn_dir  = os.path.dirname(os.path.abspath(skn_filepath))
    anim_dir = os.path.join(skn_dir, "animations")

    if not os.path.isdir(anim_dir):
        operator.report({'ERROR'}, f"No 'animations' folder found next to SKN at: {skn_dir}")
        return False

    anm_files = sorted(f for f in os.listdir(anim_dir) if f.lower().endswith('.anm'))
    if not anm_files:
        operator.report({'ERROR'}, "No ANM files found in 'animations' folder")
        return False

    # --- Back up originals (first time only) ---
    backup_dir = os.path.join(skn_dir, "Unmodified_anm_backup")
    os.makedirs(backup_dir, exist_ok=True)
    for filename in anm_files:
        backup_path = os.path.join(backup_dir, filename)
        if not os.path.exists(backup_path):
            shutil.copy2(os.path.join(anim_dir, filename), backup_path)

    # ── Build native_bone_names (Rules 1+2, matching apply_anm) ─────────────
    def _get_stored_ml(pb):
        stored = pb.get("native_matrix_local")
        if stored and len(stored) == 16:
            return mathutils.Matrix([stored[0:4], stored[4:8], stored[8:12], stored[12:16]])
        return pb.bone.matrix_local.copy()

    def _parent_shares_idx(pb):
        if pb.parent is None:
            return False
        p_idx = pb.parent.get("native_bone_index")
        m_idx = pb.get("native_bone_index")
        return (p_idx is not None and m_idx is not None and int(p_idx) == int(m_idx))

    _seen_idx: dict = {}
    native_bone_names: set = set()
    for _pb in armature_obj.pose.bones:
        _idx = _pb.get("native_bone_index")
        if _idx is None:
            continue
        _idx = int(_idx)
        _has_sfx = '.' in _pb.name
        if _idx not in _seen_idx:
            _seen_idx[_idx] = (_has_sfx, _pb.name)
            native_bone_names.add(_pb.name)
        else:
            _ex_sfx, _ex_name = _seen_idx[_idx]
            if not _has_sfx and _ex_sfx:
                native_bone_names.discard(_ex_name)
                _seen_idx[_idx] = (False, _pb.name)
                native_bone_names.add(_pb.name)
            elif _has_sfx == _ex_sfx:
                _ex_pb = armature_obj.pose.bones.get(_ex_name)
                if _parent_shares_idx(_pb) and not (_ex_pb and _parent_shares_idx(_ex_pb)):
                    native_bone_names.discard(_ex_name)
                    _seen_idx[_idx] = (_has_sfx, _pb.name)
                    native_bone_names.add(_pb.name)

    # ── Find is_custom_parent bones and pre-compute their adjustments ────────
    is_custom_parent_adjust: dict = {}

    for _pb in armature_obj.pose.bones:
        if _pb.name not in native_bone_names:
            continue
        if _pb.parent is None or _pb.parent.name in native_bone_names:
            continue  # parent is native or root — no custom intermediate

        # Walk up to find first native ancestor
        _native_anc = None
        _cur = _pb.parent
        while _cur is not None:
            if _cur.name in native_bone_names:
                _native_anc = _cur
                break
            _cur = _cur.parent
        if _native_anc is None:
            print(f"[visual_anm] WARNING: {_pb.name!r} has custom parent chain but no native ancestor!")
            continue

        _intermediate = _pb.parent
        _M_anc = _get_stored_ml(_native_anc)
        _M_int = _get_stored_ml(_intermediate)

        try:
            _b_local = _M_anc.inverted() @ _M_int
        except ValueError:
            print(f"[visual_anm] ERROR: M_anc is singular for {_native_anc.name!r} — skipping")
            continue

        _l_int_bind_L = P_inv @ _b_local @ P

        try:
            _l_int_bind_L_inv = _l_int_bind_L.inverted()
        except ValueError:
            print(f"[visual_anm] ERROR: l_int_bind_L is singular — skipping")
            continue

        _h = import_anm.Hash.elf(_pb.name)
        is_custom_parent_adjust[_h] = (_l_int_bind_L_inv, _l_int_bind_L, _intermediate.name, _pb.name)

    # ── Process each ANM directly (no Blender FK chain) ─────────────────────
    processed = 0
    failed    = []

    for filename in anm_files:
        backup_src = os.path.join(backup_dir, filename)
        dst_path   = os.path.join(anim_dir, filename)
        try:
            anm_data = import_anm.read_anm(backup_src)

            for track in anm_data.tracks:
                entry = is_custom_parent_adjust.get(track.joint_hash)
                if entry is None:
                    continue
                adj, l_int_bind_L, int_name, bone_name = entry

                for f_id, pose in track.poses.items():
                    n_t = pose.translation if pose.translation is not None else mathutils.Vector((0, 0, 0))
                    n_r = pose.rotation    if pose.rotation    is not None else mathutils.Quaternion((1, 0, 0, 0))
                    n_s = pose.scale       if pose.scale       is not None else mathutils.Vector((1, 1, 1))
                    l_native = (mathutils.Matrix.Translation(n_t) @
                                n_r.to_matrix().to_4x4() @
                                mathutils.Matrix.Diagonal(n_s.to_4d()))
                    l_visual = adj @ l_native
                    t_v, r_v, s_v = l_visual.decompose()

                    pose.translation = t_v
                    pose.rotation    = r_v.normalized()
                    pose.scale       = s_v

            export_anm.write_anm_from_data(
                dst_path, anm_data,
                fps=anm_data.fps,
                disable_scaling=disable_scaling,
            )
            processed += 1

        except Exception as e:
            import traceback
            traceback.print_exc()
            failed.append(f"{filename}: {e}")

    if failed:
        operator.report(
            {'WARNING'},
            f"Processed {processed}/{len(anm_files)} ANM(s). "
            f"Failures: {'; '.join(failed)}",
        )
    else:
        operator.report({'INFO'}, f"Processed {processed} ANM(s) for visual export")

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
        for name in bone_utils.find_unbaked_pose_offsets(armature_obj):
            operator.report({'WARNING'},
                            f"'{name}' has an unbaked Pose Mode offset that will NOT export. "
                            f"Use 'Set Offset from Pose' (N-panel) or move it in Edit Mode.")
        submesh_count, vertex_count, skn_influences = write_skn_multi(filepath, mesh_objects, armature_obj, clean_names, disable_scaling, disable_transforms, use_visual_pose, apply_object_transform=apply_object_transform, model_scale=model_scale)
        operator.report({'INFO'}, f"Exported SKN: {submesh_count} submeshes, {vertex_count} vertices, {len(skn_influences)} bone influences")

        if export_skl_file and armature_obj:
            skl_path = os.path.splitext(filepath)[0] + ".skl"
            from . import export_skl
            export_skl.write_skl(skl_path, armature_obj, disable_scaling, disable_transforms, use_visual_pose,
                                 clean_names=clean_names, apply_object_transform=apply_object_transform, model_scale=model_scale,
                                 influences=skn_influences)
            operator.report({'INFO'}, f"Exported matching SKL: {skl_path}")
            
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}